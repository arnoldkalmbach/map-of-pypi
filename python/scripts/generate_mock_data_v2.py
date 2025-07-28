import polars as pl
from pathlib import Path
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
from scipy.spatial import ConvexHull

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

def plot_graph(G: nx.DiGraph, node_data: pl.DataFrame, label_polygons: dict[int, np.ndarray], max_nodes: int = 100):
    fig, ax = plt.subplots(figsize=(16, 9))

    node_data = node_data.sort('num_downloads', descending=True).head(max_nodes)
    node_names_to_plot = node_data['name'].to_list()

    G_plot = G.subgraph(node_names_to_plot)
    pos_plot = {
        node: (pos[0], pos[1]) for node, pos in node_data[['name', 'umap_embedding']].iter_rows(named=False)
    }
            
    nx.draw(
        G_plot, pos=pos_plot, node_color=node_data['cluster_label'].to_list(),
        node_size=50, width=0.5, alpha=0.7, with_labels=True, ax=ax, cmap=plt.cm.tab10
    )
    for label, polygon in label_polygons.items():
        ax.fill(polygon[:, 0], polygon[:, 1], alpha=0.2, color=plt.cm.tab10(label))
    plt.show()

def get_embeddings(descriptions: list[str], model_name: str) -> np.ndarray:
    model = SentenceTransformer(model_name)
    descriptions_notnull = [
        d if d else "" for d in descriptions
    ]
    return model.encode(descriptions_notnull, show_progress_bar=True)

def propagate_neighbor_embeddings(G: nx.DiGraph, embeddings: np.ndarray) -> np.ndarray:
    adjacency = nx.adjacency_matrix(G).T             # In-neighbors = Things that depend on this package
    adjacency = adjacency + 100 * scipy.sparse.eye(adjacency.shape[0]) # Add self-loops so we don't forget our own embedding
    sums = adjacency.dot(embeddings)
    row_sums = np.array(adjacency.sum(axis=1)).ravel()
    embeddings_new = sums / row_sums[:, None]
    return embeddings_new


def get_umap_embeddings(descriptions: list[str], G: nx.DiGraph, propagation_steps: int = 1, umap_kwargs: dict[str, Any] = dict()) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    # TODO: Cache initial embeddings
    initial_embeddings = get_embeddings(descriptions, model_name='all-mpnet-base-v2')

    reducer = umap.UMAP(**umap_kwargs)

    propagated_embeddings = initial_embeddings.copy()
    for _ in range(propagation_steps):
        propagated_embeddings = propagate_neighbor_embeddings(G, propagated_embeddings)

    xy_embeddings = reducer.fit_transform(propagated_embeddings).astype(float)
    return initial_embeddings, propagated_embeddings, xy_embeddings


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
        propagation_steps=25, # More propagation = embeddings more blended over dependencies
        umap_kwargs=dict(n_components=2, n_neighbors=50, min_dist=0.0, random_state=42)
    )
    clusterer = hdbscan.HDBSCAN(
        min_samples=3,
        min_cluster_size=10,
        prediction_data=True,
    )

    clusterer = clusterer.fit(umap_embeddings)
    node_data = pl.DataFrame({
        'name': list(G.nodes()),
        'cluster_label': clusterer.labels_,
        'description_embedding': description_embeddings,
        'propagated_embedding': propagated_embeddings,
        'umap_embedding': umap_embeddings,
    }).join(df[['name', 'description', 'num_downloads']], on='name')
    
    print("Calculating polygons from mesh...")
    minx, maxx = umap_embeddings[:, 0].min(), umap_embeddings[:, 0].max()
    miny, maxy = umap_embeddings[:, 1].min(), umap_embeddings[:, 1].max()
    mesh_x, mesh_y = np.meshgrid(np.linspace(minx, maxx, 500), np.linspace(miny, maxy, 500))
    mesh_labels, _ = hdbscan.approximate_predict(clusterer, np.vstack([mesh_x.flatten(), mesh_y.flatten()]).T)
    mesh_labels = mesh_labels.reshape(mesh_x.shape).astype(float)
    mesh_labels[mesh_labels == -1] = np.nan
    polygons = {}
    for label in np.unique(mesh_labels):
        if np.isnan(label):
            continue
        label = int(label)
        xx = mesh_x[mesh_labels == label]
        yy = mesh_y[mesh_labels == label]
        if len(xx) > 10:
            print(xx.shape, yy.shape)
            hull = ConvexHull(np.vstack([xx, yy]).T)
            polygons[label] = hull.points[hull.vertices]

    print("Calculating layout...")
    root = list(G.nodes())[0]
    plot = plot_graph(G, node_data, label_polygons=polygons)
    
    initial_pos = {
        node: (embedding[0], embedding[1]) for node, embedding in node_data[['name', 'umap_embedding']].iter_rows(named=False)
    }

    # Export dataset in mock-data structure via helper util
    export_mock_data(
        G,
        node_data,
        label_polygons=polygons,
        data_version='v2',
    )