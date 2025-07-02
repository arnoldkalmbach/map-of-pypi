import polars as pl
import networkx as nx
import numpy as np
import re
import matplotlib.pyplot as plt

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

def plot_graph(G: nx.DiGraph, pos: dict[str, tuple[float, float]]):
    fig, ax = plt.subplots(figsize=(16, 9))

    distance_to_leaf = min_weighted_distance_to_leaf(G)
    node_colors = [np.log1p(distance_to_leaf.get(node, 0)) for node in G.nodes()]
            
    nx.draw(G, pos=pos, node_size=50, width=0.5, alpha=0.7, with_labels=True, ax=ax, node_color=node_colors, cmap=plt.cm.viridis)
    plt.show()



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
        .head(500)
    )
    print(f"Loaded {len(df)} packages")

    edges = df[['name', 'requires_packages', 'num_downloads']].explode('requires_packages')
    nodes = df[['name', 'description']]

    # Prune dependencies on nodes outside the list
    edges = edges.join(nodes[['name']], left_on='requires_packages', right_on='name', how='inner')
    all_packages_with_edges = pl.concat((edges['name'], edges['requires_packages'])).unique()
    nodes = nodes.join(pl.DataFrame([all_packages_with_edges]), on='name')

    G = nx.DiGraph()
    G.add_nodes_from(nodes['name'])

    edge_data = [
        (row[0], row[1], {'weight': row[2]})
        for row in edges[['name', 'requires_packages', 'num_downloads']].iter_rows(named=False)
    ]
    G.add_edges_from(edge_data)
    print(f"Graph created with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges")

    print("Calculating layout...")
    # Initialize the nodes in a vertical line, from most depended to least depended
    # Add some random noise to the x-coordinate to break symmetry for the layout algorithm
    initial_pos = {
        node: (np.random.normal(scale=.01), G.in_degree(node)) for i, node in enumerate(G.nodes())
    }
    pos = nx.spring_layout(G, k=1, iterations=100, pos=initial_pos)

    print(f"Layout calculated for {len(pos)} nodes")
    plot = plot_graph(G, pos)
    