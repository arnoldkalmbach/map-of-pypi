import polars as pl
import networkx as nx
import numpy as np
import re
import matplotlib.pyplot as plt
import umap
from sentence_transformers import SentenceTransformer
from typing import Any
from mock_data_export import export_mock_data
import scipy.sparse
import hdbscan

# Parses the package name from a requires_dist string
PACKAGE_RE = r"^\s*([A-Za-z0-9][-.\w]*(?:\[[A-Za-z0-9_\-.,]+\])?)"

def strip_html(html_string):
    """Strip HTML tags from a string to get plain text"""
    if not html_string:
        return ""
    # Remove HTML tags
    clean = re.compile('<.*?>')
    return re.sub(clean, '', html_string).strip()

def min_weighted_distance_to_leaf(G: nx.DiGraph, weight='weight'):

    leaves = [n for n in G.nodes if G.out_degree(n) == 0]
    
    if not leaves:
        return {}

    rev_G = G.reverse(copy=False)

    return nx.multi_source_dijkstra_path_length(rev_G, leaves)

def plot_graph(G: nx.DiGraph, pos: dict[str, tuple[float, float]], cluster_labels: dict[str, int], mesh: tuple[np.ndarray, np.ndarray, np.ndarray]):
    fig, ax = plt.subplots(figsize=(16, 9))

    # Filter to only show top 1000 nodes
    nodes_to_keep = list(G.nodes())[:1000]
    G_plot = G.subgraph(nodes_to_keep)
    pos_plot = {
        node: (pos[node][0], pos[node][1]) for node in nodes_to_keep
    }

    # distance_to_leaf = min_weighted_distance_to_leaf(G)
    node_colors = [cluster_labels.get(node, 0) for node in G_plot.nodes()]
            
    nx.draw(G_plot, pos=pos_plot, node_size=50, width=0.5, alpha=0.7, with_labels=True, ax=ax, node_color=node_colors, cmap=plt.cm.tab10)
    plt.contourf(mesh[0], mesh[1], mesh[2], cmap=plt.cm.tab10)
    plt.show()

def get_embeddings(descriptions: list[str], model_name: str) -> np.ndarray:
    model = SentenceTransformer(model_name)
    descriptions_notnull = [
        d if d else "" for d in descriptions
    ]
    return model.encode(descriptions_notnull, show_progress_bar=True)

def propagate_neighbor_embeddings(G: nx.DiGraph, embeddings: np.ndarray) -> np.ndarray:
    adjacency = nx.adjacency_matrix(G).T             # In-neighbors = Things that depend on this package
    adjacency = adjacency + scipy.sparse.eye(adjacency.shape[0]) # Add self-loops so we don't forget our own embedding
    sums = adjacency.dot(embeddings)
    row_sums = np.array(adjacency.sum(axis=1)).ravel()
    embeddings_new = sums / row_sums[:, None]
    return embeddings_new


def get_umap_embeddings(descriptions: list[str], G: nx.DiGraph, propagation_steps: int = 1, umap_kwargs: dict[str, Any] = dict()) -> np.ndarray:
    initial_embeddings = get_embeddings(descriptions, model_name='all-mpnet-base-v2')
    reducer = umap.UMAP(**umap_kwargs)

    propagated_embeddings = initial_embeddings.copy()
    for _ in range(propagation_steps):
        propagated_embeddings = propagate_neighbor_embeddings(G, propagated_embeddings)
    return initial_embeddings, propagated_embeddings, reducer.fit_transform(propagated_embeddings).astype(float)


if __name__ == "__main__":
    print("Loading data...")
    df = (
        pl.read_ndjson("../public/mock-data/raw_data/bq-interesting-science-packages_20250624.jsonl")
        .with_columns(
            pl.col('requires_dist').list.eval(
                pl.element().str.extract(PACKAGE_RE, 1)
            ).alias('requires_packages'),
            pl.col('num_downloads').cast(pl.Int64)
        )
        .sort('num_downloads', descending=True)
    )
    print(f"Loaded {len(df)} packages")

    edges = df[['name', 'requires_packages', 'num_downloads']].explode('requires_packages')
    nodes = df[['name', 'description']]

    # Prune dependencies on nodes outside the list
    edges = edges.join(nodes[['name']], left_on='requires_packages', right_on='name', how='inner')
    all_packages_with_edges = pl.concat((edges['name'], edges['requires_packages'])).unique(maintain_order=True)
    nodes = nodes.join(pl.DataFrame([all_packages_with_edges]), on='name')

    G = nx.DiGraph()
    G.add_nodes_from(nodes['name'])

    edge_data = [
        (row[0], row[1], {'weight': row[2]})
        for row in edges[['name', 'requires_packages', 'num_downloads']].iter_rows(named=False)
    ]
    G.add_edges_from(edge_data)
    print(f"Graph created with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges")

    print("Calculating embeddings...")
    description_embeddings, propagated_embeddings, umap_embeddings = get_umap_embeddings(
        nodes['description'].to_list(),
        G,
        umap_kwargs=dict(n_components=2, n_neighbors=50, min_dist=0.0, random_state=42)
    )
    clusterer = hdbscan.HDBSCAN(
        min_samples=3,
        min_cluster_size=10,
        prediction_data=True,
    )

    clusterer = clusterer.fit(umap_embeddings)
    cluster_labels = clusterer.labels_
    
    minx, maxx = umap_embeddings[:, 0].min(), umap_embeddings[:, 0].max()
    miny, maxy = umap_embeddings[:, 1].min(), umap_embeddings[:, 1].max()
    mesh_x, mesh_y = np.meshgrid(np.linspace(minx, maxx, 100), np.linspace(miny, maxy, 100))
    mesh_labels, _ = hdbscan.approximate_predict(clusterer, np.vstack([mesh_x.flatten(), mesh_y.flatten()]).T)
    mesh_labels = mesh_labels.reshape(mesh_x.shape).astype(float)
    mesh_labels[mesh_labels == -1] = np.nan

    print(
        pl.Series(cluster_labels).value_counts().to_dicts()
    )

    print("Calculating layout...")
    initial_pos = {
        node: (umap_embeddings[i, 0], umap_embeddings[i, 1]) for i, node in enumerate(G.nodes())
    }
    cluster_labels = {
        node: cluster_labels[i] for i, node in enumerate(G.nodes())
    }
    root = list(G.nodes())[0]
    pos = initial_pos
    # pos = nx.spring_layout(G, k=1, iterations=3, pos=initial_pos, fixed=[root])

    print(f"Layout calculated for {len(pos)} nodes")

    # Export dataset in mock-data structure via helper util
    export_mock_data(
        G,
        pos,
        nodes['name'].to_list(),
        data_version='v2',
    )

    plot = plot_graph(G, pos, cluster_labels=cluster_labels, mesh=(mesh_x, mesh_y, mesh_labels))