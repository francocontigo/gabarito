"""Generate the workflow diagrams used in the README, in a light and a dark version.

    uv run --with diagrams python docs/make_diagrams.py

Needs Graphviz (`dot`) on the PATH. Writes PNG files to docs/diagrams.
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.analytics import Athena, Redshift
from diagrams.aws.migration import TransferForSftp
from diagrams.aws.storage import S3
from diagrams.onprem.database import PostgreSQL
from diagrams.programming.flowchart import Document, MultipleDocuments
from diagrams.programming.language import Python
from diagrams.saas.analytics import Snowflake

OUT = Path(__file__).parent / "diagrams"
FONT = "Arial"
THEMES = {
    "": {
        "bg": "#ffffff",
        "text": "#1f2328",
        "line": "#57606a",
        "box": "#f3f8fc",
        "border": "#9fc5e0",
        "bad": "#b42318",
    },
    "-dark": {
        "bg": "#0d1117",
        "text": "#e6edf3",
        "line": "#9198a1",
        "box": "#161b22",
        "border": "#3d444d",
        "bad": "#ff7b72",
    },
}
theme = THEMES[""]


def diagram(name: str) -> Diagram:
    # Few columns and large text: GitHub shrinks a README image to about 850 px wide.
    return Diagram(
        "",
        filename=str(OUT / name),
        outformat="png",
        show=False,
        direction="LR",
        graph_attr={
            "splines": "spline",
            "bgcolor": theme["bg"],
            "pad": "0.5",
            "nodesep": "0.9",
            "ranksep": "1.3",
            "dpi": "160",
        },
        node_attr={"fontname": FONT, "fontsize": "20", "fontcolor": theme["text"]},
        edge_attr={"color": theme["line"], "penwidth": "2.2", "arrowsize": "1.1"},
    )


def node(kind: type[Node], label: str) -> Node:
    """A node tall enough that the label sits below the icon instead of over it."""
    return kind(label, height=str(2.35 + 0.5 * label.count("\n")))


def edge(label: str = "", bad: bool = False, **attrs: str) -> Edge:
    """An edge labelled in the same font as the nodes (the library defaults to another one)."""
    color = theme["bad"] if bad else theme["line"]
    style = {"style": "dashed"} if bad else {}
    return Edge(
        label=f" {label} ",
        fontname=FONT,
        fontsize="19",
        fontcolor=theme["bad"] if bad else theme["text"],
        color=color,
        **style,
        **attrs,
    )


def box(label: str) -> Cluster:
    return Cluster(
        label,
        graph_attr={
            "fontname": f"{FONT} Bold",
            "fontsize": "20",
            "fontcolor": theme["text"],
            "bgcolor": theme["box"],
            "pencolor": theme["border"],
            "margin": "22",
        },
    )


def load(name: str) -> None:
    with diagram(name):
        client = node(TransferForSftp, "client\nSFTP")
        with box("validate"):
            tool = node(Python, "gabarito\nlayout.yml")
        db = node(PostgreSQL, "Postgres\ntyped table")
        rejects = node(Document, "rejects.csv\nline + reason")

        client >> edge("cobranca.txt") >> tool
        tool >> edge("check, then load") >> db
        tool >> edge("rejects", bad=True) >> rejects


def lake(name: str) -> None:
    with diagram(name):
        landing = node(S3, "s3://landing\n*.rem, *.ret")
        with box("validate and convert"):
            tool = node(Python, "gabarito\ncnab240")
        parquet = node(S3, "s3://lake\nParquet")
        with box("reads Parquet"):
            engines = [node(Athena, "Athena"), node(Redshift, "Redshift"), node(Snowflake, "Snowflake")]

        landing >> edge("check") >> tool >> edge("export") >> parquet
        for engine in engines:
            parquet >> edge() >> engine


def back(name: str) -> None:
    with diagram(name):
        db = node(PostgreSQL, "Postgres\naccepted rows")
        with box("write and re-check"):
            tool = node(Python, "gabarito\nretorno.yml")
        out = node(MultipleDocuments, "retorno.txt\nheader, lines, trailer")
        client = node(TransferForSftp, "client or bank\nSFTP")

        db >> edge("SELECT") >> tool >> edge("write") >> out >> edge("sent back") >> client


OUT.mkdir(exist_ok=True)
for suffix, colors in THEMES.items():
    theme = colors
    load(f"load{suffix}")
    lake(f"lake{suffix}")
    back(f"return{suffix}")
