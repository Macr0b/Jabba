"""Consommation des outils : lecture de « docker stats », somme par outil."""

import json

import pytest

from services import tool_usage


@pytest.mark.parametrize(
    "texte, byte_count",
    [("512MiB", 512 * 1024**2), ("1.5GiB", int(1.5 * 1024**3)), ("800kB", 800_000), ("12B", 12), ("", 0), ("n/a", 0)],
)
def test_sizes(texte, byte_count):
    assert tool_usage.parse_size(texte) == byte_count


def test_sum_per_tool():
    output = (
        "\n".join(
            json.dumps(line)
            for line in [
                {"Name": "nextcloud", "CPUPerc": "2.50%", "MemUsage": "500MiB / 7.6GiB"},
                {"Name": "nextcloud-db", "CPUPerc": "0.75%", "MemUsage": "100MiB / 7.6GiB"},
                {"Name": "jellyfin", "CPUPerc": "120.00%", "MemUsage": "1GiB / 7.6GiB"},
                {"Name": "orphelin", "CPUPerc": "1%", "MemUsage": "1MiB / 1GiB"},
            ]
        )
        + "\nligne illisible\n"
    )
    stats = tool_usage.parse_stats(output)
    tools = [
        {"id": "nextcloud", "conteneurs": [{"nom": "nextcloud"}, {"nom": "nextcloud-db"}]},
        {"id": "jellyfin", "conteneurs": [{"nom": "jellyfin"}]},
        {"id": "dockge", "conteneurs": [{"nom": "dockge"}]},  # arrêté : pas mesuré
    ]
    assert tool_usage.usage_by_tool(tools, stats) == {
        "nextcloud": {"processeur": 3.2, "memoire": 600 * 1024**2},
        "jellyfin": {"processeur": 120.0, "memoire": 1024**3},
    }
