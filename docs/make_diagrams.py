"""Generate the workflow diagrams used in the README.

    uv run --with diagrams python docs/make_diagrams.py

Needs Graphviz (`dot`) on the PATH. Writes PNG files next to this script.
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.analytics import Athena, Redshift
from diagrams.aws.storage import S3
from diagrams.onprem.client import Client
from diagrams.onprem.database import PostgreSQL
from diagrams.programming.flowchart import Document, MultipleDocuments
from diagrams.programming.language import Python
from diagrams.saas.analytics import Snowflake

HERE = Path(__file__).parent
# Sized to stay readable at the width GitHub renders a README (about 850 px):
# large text, little space between nodes.
FONT = "Arial"
GRAPH = {
    "splines": "spline",
    "bgcolor": "white",
    "pad": "0.9",
    "nodesep": "0.5",
    "ranksep": "0.55",
    "dpi": "150",
}
NODE = {"fontname": FONT, "fontsize": "24", "fontcolor": "#1f2328"}
EDGE = {"color": "#57606a", "penwidth": "2.2", "arrowsize": "1.2"}
CLUSTER = {"fontname": f"{FONT} Bold", "fontsize": "24", "bgcolor": "#eef6fc", "pencolor": "#9fc5e0", "margin": "20"}
BAD = {"color": "#b42318", "fontcolor": "#b42318", "style": "dashed"}


def diagram(name: str) -> Diagram:
    return Diagram(
        "",
        filename=str(HERE / name),
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=GRAPH,
        node_attr=NODE,
        edge_attr=EDGE,
    )


def node(kind: type[Node], label: str) -> Node:
    """A node tall enough that the larger label sits below the icon instead of over it."""
    return kind(label, height=str(2.55 + 0.6 * label.count("\n")))


def edge(label: str = "", **attrs: str) -> Edge:
    """An edge label in the same font as the nodes (the library defaults to another one)."""
    return Edge(label=f"  {label}  " if label else "", fontname=FONT, fontsize="21", **attrs)


with diagram("workflow-load"):
    client = node(Client, "client")
    file = node(Document, "cobranca.txt\nfixed-width")
    with Cluster("gabarito  ·  layout.yml", graph_attr=CLUSTER):
        check = node(Python, "check")
        load = node(Python, "sql / load")
    rejects = node(Document, "rejects.csv\nline, reason")
    db = node(PostgreSQL, "Postgres\ntyped table")

    client >> edge("sends") >> file >> check >> edge("file matches") >> load
    load >> edge("ATTACH") >> db
    check >> edge("rejects", **BAD) >> rejects


with diagram("workflow-lake"):
    landing = node(S3, "s3://landing\n*.txt, *.rem")
    with Cluster("gabarito  ·  cnab240", graph_attr=CLUSTER):
        check = node(Python, "check")
        export = node(Python, "export")
    parquet = node(S3, "s3://lake\nParquet")
    with Cluster("anything that reads Parquet", graph_attr=CLUSTER):
        engines = [node(Athena, "Athena"), node(Redshift, "Redshift"), node(Snowflake, "Snowflake")]

    landing >> edge("glob") >> check >> export >> parquet
    for engine in engines:
        parquet >> engine


with diagram("workflow-return"):
    db = node(PostgreSQL, "Postgres\naccepted rows")
    with Cluster("gabarito  ·  retorno.yml", graph_attr=CLUSTER):
        write = node(Python, "write")
        check = node(Python, "check")
    out = node(MultipleDocuments, "retorno.txt\nheader, lines, trailer")
    client = node(Client, "client or bank")

    db >> edge("SELECT") >> write >> out >> edge("same layout") >> check
    out >> edge("sent back") >> client
