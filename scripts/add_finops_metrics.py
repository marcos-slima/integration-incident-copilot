#!/usr/bin/env python3
"""Adiciona painéis de métricas FinOps ao dashboard COI (DA-70).

Este script estende o dashboard_coi.json com painéis para:
- llm_requests_total: chamadas LLM por provider/model
- llm_tokens_total: tokens (input/output) por provider/model
- llm_cost_usd_total: custo em USD por provider/model

Uso:
    uv run python scripts/add_finops_metrics.py
"""

import json
from pathlib import Path


def create_panel_requests() -> dict:
    """Cria painel de chamadas LLM por provider/model."""
    return {
        "datasource": {"type": "grafana-postgresql-datasource", "uid": "postgres_iic"},
        "fieldConfig": {
            "defaults": {"unit": "short"},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "provider"},
                    "properties": [{"id": "displayName", "value": "Provider"}],
                },
                {
                    "matcher": {"id": "byName", "options": "model"},
                    "properties": [{"id": "displayName", "value": "Model"}],
                },
                {
                    "matcher": {"id": "byName", "options": "requests"},
                    "properties": [{"id": "displayName", "value": "Chamadas"}],
                },
            ],
        },
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 28},
        "id": 12,
        "options": {
            "tooltip": {"mode": "multi"},
            "legend": {"displayMode": "list", "placement": "bottom"},
        },
        "title": "Chamadas LLM por Provider/Model",
        "type": "timeseries",
        "targets": [
            {
                "datasource": {"type": "grafana-postgresql-datasource", "uid": "postgres_iic"},
                "rawSql": (
                    "SELECT "
                    "$__timeGroupAlias(created_at, '5m'), "
                    "provider, "
                    "model, "
                    "COUNT(*) as requests "
                    "FROM llm_requests "
                    "WHERE $__timeFilter(created_at) "
                    "GROUP BY 1, 2, 3 ORDER BY 1"
                ),
                "format": "time_series",
            }
        ],
    }


def create_panel_tokens() -> dict:
    """Cria painel de tokens LLM por provider/model."""
    return {
        "datasource": {"type": "grafana-postgresql-datasource", "uid": "postgres_iic"},
        "fieldConfig": {
            "defaults": {"unit": "short"},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "provider"},
                    "properties": [{"id": "displayName", "value": "Provider"}],
                },
                {
                    "matcher": {"id": "byName", "options": "model"},
                    "properties": [{"id": "displayName", "value": "Model"}],
                },
                {
                    "matcher": {"id": "byName", "options": "tokens"},
                    "properties": [{"id": "displayName", "value": "Tokens"}],
                },
            ],
        },
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 28},
        "id": 13,
        "options": {
            "tooltip": {"mode": "multi"},
            "legend": {"displayMode": "list", "placement": "bottom"},
        },
        "title": "Tokens LLM por Provider/Model",
        "type": "timeseries",
        "targets": [
            {
                "datasource": {"type": "grafana-postgresql-datasource", "uid": "postgres_iic"},
                "rawSql": (
                    "SELECT "
                    "$__timeGroupAlias(created_at, '5m'), "
                    "provider, "
                    "model, "
                    "SUM(input_tokens + output_tokens) as tokens "
                    "FROM llm_tokens "
                    "WHERE $__timeFilter(created_at) "
                    "GROUP BY 1, 2, 3 ORDER BY 1"
                ),
                "format": "time_series",
            }
        ],
    }


def create_panel_cost() -> dict:
    """Cria painel de custo LLM por provider/model."""
    return {
        "datasource": {"type": "grafana-postgresql-datasource", "uid": "postgres_iic"},
        "fieldConfig": {
            "defaults": {"unit": "USD"},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "provider"},
                    "properties": [{"id": "displayName", "value": "Provider"}],
                },
                {
                    "matcher": {"id": "byName", "options": "model"},
                    "properties": [{"id": "displayName", "value": "Model"}],
                },
                {
                    "matcher": {"id": "byName", "options": "cost_usd"},
                    "properties": [{"id": "displayName", "value": "Custo (USD)"}],
                },
            ],
        },
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": 36},
        "id": 14,
        "options": {
            "tooltip": {"mode": "multi"},
            "legend": {"displayMode": "list", "placement": "bottom"},
        },
        "title": "Custo LLM por Provider/Model (USD)",
        "type": "timeseries",
        "targets": [
            {
                "datasource": {"type": "grafana-postgresql-datasource", "uid": "postgres_iic"},
                "rawSql": (
                    "SELECT "
                    "$__timeGroupAlias(created_at, '5m'), "
                    "provider, "
                    "model, "
                    "SUM(cost_usd) as cost_usd "
                    "FROM llm_cost "
                    "WHERE $__timeFilter(created_at) "
                    "GROUP BY 1, 2, 3 ORDER BY 1"
                ),
                "format": "time_series",
            }
        ],
    }


def main():
    """Adiciona painéis FinOps ao dashboard."""
    dashboard_path = Path("deploy/grafana/dashboards/dashboard_coi.json")

    with open(dashboard_path) as f:
        dashboard = json.load(f)

    # Adiciona painéis ao final da lista
    panels = dashboard["panels"]
    panels.append(create_panel_requests())
    panels.append(create_panel_tokens())
    panels.append(create_panel_cost())

    # Atualiza schemaVersion
    dashboard["schemaVersion"] = dashboard.get("schemaVersion", 39) + 1

    # Salva o dashboard atualizado
    with open(dashboard_path, "w") as f:
        json.dump(dashboard, f, indent=2)

    print(f"Dashboard atualizado: {dashboard_path}")
    print("Painéis adicionados: 3 (requests, tokens, cost)")
    print(f"Schema version: {dashboard['schemaVersion']}")


if __name__ == "__main__":
    main()
