import asyncio
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent.tools.chart_generator import create_generate_chart_tool
from agent.tools.local_charts import render_chart
from api_view.api.artifacts import router

@pytest.mark.parametrize("kind", ["bar", "column", "line", "pie"])
def test_local_chart_without_external_mcp(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("ERP_ARTIFACT_DIR", str(tmp_path))
    tool, _ = create_generate_chart_tool([])
    result = asyncio.run(tool.ainvoke({"chart_type":kind,"chart_config":{"title":"供应商等级", "data":[{"category":"A级","value":7},{"category":"B级","value":9}]}}))
    assert "/api/artifacts/" in result and ".svg" in result
    files=list(tmp_path.glob("*.svg"))
    assert len(files)==1
    ET.fromstring(files[0].read_text(encoding="utf-8"))
    assert "供应商等级" in files[0].read_text(encoding="utf-8")

def test_chart_escapes_labels():
    svg=render_chart("bar", {"data":[{"category":"<script>alert(1)</script>","value":2}]})
    assert "<script>" not in svg
    ET.fromstring(svg)

@pytest.mark.parametrize("data", [[], [{"category":"a","value":float("nan")}], [{"category":"a","value":"oops"}]])
def test_invalid_data(data):
    with pytest.raises(ValueError): render_chart("bar", {"data":data})

def test_artifact_access(tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_ARTIFACT_DIR", str(tmp_path))
    name="a"*32+".svg"
    (tmp_path/name).write_text(render_chart("pie", {"data":[{"category":"A","value":1}]}),encoding="utf-8")
    app=FastAPI(); app.include_router(router,prefix="/api")
    with TestClient(app) as client:
        response=client.get("/api/artifacts/"+name)
        assert response.status_code==200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert "sandbox" in response.headers["content-security-policy"]
        assert client.get("/api/artifacts/not-a-public-file.svg").status_code==404
        assert client.get("/api/artifacts/"+"b"*32+".md").status_code==404
        (tmp_path/("c"*32+".md")).write_text("report")
        assert "attachment" in client.get("/api/artifacts/"+"c"*32+".md").headers["content-disposition"]


def test_report_has_web_download_link(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent.tools.download_sandbox_file import create_download_tool
    monkeypatch.setenv("ERP_ARTIFACT_DIR", str(tmp_path / "public"))
    class Backend:
        def download_files(self, paths):
            return [SimpleNamespace(error=None,content="# 完整报告".encode())]
    tool=create_download_tool(Backend(), tmp_path / "downloads")
    result=tool.invoke({"sandbox_path":"/analysis/report.md"})
    assert "/api/artifacts/" in result and ".md" in result
    assert str(tmp_path) not in result
    assert len(list((tmp_path / "public").glob("*.md")))==1

def test_negative_pie_and_grouped_data_rejected():
    with pytest.raises(ValueError): render_chart("pie", {"data":[{"category":"A","value":-1}]})
    with pytest.raises(ValueError): render_chart("bar", {"group":True,"data":[{"category":"A","value":1}]})
