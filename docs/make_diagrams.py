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
# Sized to stay readable at the width GitHub renders a README (about 850 px).
GRAPH = {
    "splines": "spline",
    "bgcolor": "white",
    "pad": "0.3",
    "nodesep": "0.6",
    "ranksep": "0.75",
    "dpi": "150",
}
NODE = {"fontname": "Helvetica", "fontsize": "18"}
EDGE = {"fontname": "Helvetica", "fontsize": "16", "color": "#57606a", "penwidth": "1.8"}
CLUSTER = {"fontname": "Helvetica-Bold", "fontsize": "18", "bgcolor": "#eef6fc", "pencolor": "#9fc5e0", "margin": "18"}
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
    return kind(label, height=str(2.3 + 0.45 * label.count("\n")))


with diagram("workflow-load"):
    client = node(Client, "client")
    file = node(Document, "cobranca.txt\nfixed-width")
    with Cluster("gabarito  ·  layout.yml", graph_attr=CLUSTER):
        check = node(Python, "check")
        load = node(Python, "sql / load")
    rejects = node(Document, "rejects.csv\nline, reason")
    db = node(PostgreSQL, "Postgres\ntyped table")

    client >> Edge(label="sends") >> file >> check >> Edge(label="file matches") >> load
    load >> Edge(label="ATTACH") >> db
    check >> Edge(label="rejects", **BAD) >> rejects


with diagram("workflow-lake"):
    landing = node(S3, "s3://landing\n*.txt, *.rem")
    with Cluster("gabarito  ·  cnab240", graph_attr=CLUSTER):
        check = node(Python, "check")
        export = node(Python, "export")
    parquet = node(S3, "s3://lake\nParquet")
    with Cluster("anything that reads Parquet", graph_attr=CLUSTER):
        engines = [node(Athena, "Athena"), node(Redshift, "Redshift"), node(Snowflake, "Snowflake")]

    landing >> Edge(label="glob") >> check >> export >> parquet
    for engine in engines:
        parquet >> engine


with diagram("workflow-return"):
    db = node(PostgreSQL, "Postgres\naccepted rows")
    with Cluster("gabarito  ·  retorno.yml", graph_attr=CLUSTER):
        write = node(Python, "write")
        check = node(Python, "check")
    out = node(MultipleDocuments, "retorno.txt\nheader, lines, trailer")
    client = node(Client, "client or bank")

    db >> Edge(label="SELECT") >> write >> out >> Edge(label="same layout") >> check
    out >> Edge(label="sent back") >> client
