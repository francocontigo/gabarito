"""Generate the workflow diagrams used in the README.

    uv run --with diagrams python docs/make_diagrams.py

Needs Graphviz (`dot`) on the PATH. Writes PNG files next to this script.
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.analytics import Athena, Redshift
from diagrams.aws.storage import S3
from diagrams.onprem.client import Client
from diagrams.onprem.database import PostgreSQL
from diagrams.programming.flowchart import Document, MultipleDocuments
from diagrams.programming.language import Python
from diagrams.saas.analytics import Snowflake

HERE = Path(__file__).parent
GRAPH = {
    "splines": "spline",
    "bgcolor": "white",
    "pad": "0.4",
    "nodesep": "0.7",
    "ranksep": "1.0",
    "fontname": "Helvetica",
    "fontsize": "15",
}
NODE = {"fontname": "Helvetica", "fontsize": "12"}
EDGE = {"fontname": "Helvetica", "fontsize": "11", "color": "#57606a"}
BAD = {"color": "#b42318", "fontcolor": "#b42318", "style": "dashed"}


def diagram(name: str, title: str) -> Diagram:
    return Diagram(
        title,
        filename=str(HERE / name),
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=GRAPH,
        node_attr=NODE,
        edge_attr=EDGE,
    )


with diagram("workflow-load", "Validate a client file and load it into the database"):
    client = Client("client")
    file = Document("cobranca.txt\nfixed-width")
    with Cluster("gabarito  ·  layout.yml"):
        check = Python("check")
        load = Python("sql / load")
    rejects = Document("rejects.csv\nline, reason")
    db = PostgreSQL("Postgres\ntyped table")

    client >> Edge(label="sends") >> file >> check >> Edge(label="file matches") >> load
    load >> Edge(label="ATTACH") >> db
    check >> Edge(label="rejects", **BAD) >> rejects


with diagram("workflow-lake", "From a landing bucket to the warehouse"):
    landing = S3("s3://landing\n*.txt, *.rem")
    with Cluster("gabarito  ·  cnab240"):
        check = Python("check")
        export = Python("export")
    parquet = S3("s3://lake\nParquet per record")
    with Cluster("anything that reads Parquet"):
        engines = [Athena("Athena"), Redshift("Redshift"), Snowflake("Snowflake")]

    landing >> Edge(label="glob") >> check >> export >> parquet
    for engine in engines:
        parquet >> engine


with diagram("workflow-return", "Write the return file from a query"):
    db = PostgreSQL("Postgres\naccepted rows")
    with Cluster("gabarito  ·  retorno.yml"):
        write = Python("write")
        check = Python("check")
    out = MultipleDocuments("retorno.txt\nheader, lines, trailer")
    client = Client("client or bank")

    db >> Edge(label="SELECT") >> write >> out >> Edge(label="same layout") >> check
    out >> Edge(label="sent back") >> client
