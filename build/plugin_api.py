#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""File-based plugin bridge. No credentials, network server, or arbitrary dispatch.

python build/plugin_api.py --request request.json --response response.json
Request: {"operation": "command", "payload": {...}}
Response: {"status": 200, "body": {...}}
FORGE_DATA_DIR selects mutable data root; no env value is returned.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import studio_api
import studio_store
from ext.freeform import routes as ff


def dispatch(operation, payload):
    if not isinstance(payload, dict):
        return 400, {"error": "payload must be an object"}
    operations = {
        "web_status": lambda p: (200, __import__('web_capture').status()),
        "web_capture": lambda p: __import__('web_routes').capture(p),
        "web_restore": web_restore_operation,
        "command": studio_api.forge_from_command,
        "spec": spec_operation,
        "screenshot": studio_api.forge_from_screenshot,
        "screenshot_spec": lambda p: studio_api.run_screenshot_pipeline(p["spec"], workspace=p.get("workspace")),
        "html": studio_api.forge_from_html,
        "image": studio_api.forge_from_image,
        "parse": lambda p: studio_api.parse_command(p.get("command"), p.get("templateId")),
        "templates": lambda p: (200, {"templates": studio_api.list_templates()}),
        "template_save": lambda p: studio_api.save_template(p.get("run"), p.get("name")),
        "template_delete": lambda p: studio_api.delete_template(p.get("id")),
        "workspaces": lambda p: (200, {"workspaces": studio_store.list_workspaces()}),
        "workspace_create": lambda p: studio_store.create_workspace(p.get("name")),
        "workspace_rename": lambda p: studio_store.rename_workspace(p.get("id"), p.get("name")),
        "workspace_delete": lambda p: studio_store.delete_workspace(p.get("id")),
        "runs": lambda p: (200, {"runs": studio_api.list_runs(limit=min(500, int(p.get("limit", 200))), workspace=p.get("workspace"), status=p.get("status"), q=p.get("q"))}),
        "run_delete": lambda p: studio_store.delete_runs(p["runs"]) if isinstance(p.get("runs"), list) else studio_store.delete_run(p.get("run")),
        "zip": zip_operation,
        "freeform_generate": ff.generate,
        "freeform_html": ff.from_html,
        "freeform_image": ff.from_image,
        "freeform_parse": ff.parse_preview,
        "freeform_templates": lambda p: (200, {"templates": ff.ff_templates()}),
        "freeform_template_save": lambda p: ff.save_template(p.get("run"), p.get("name")),
        "freeform_template_delete": lambda p: ff.delete_template(p.get("id")),
    }
    fn = operations.get(operation)
    if fn is None:
        return 400, {"error": "Unknown operation", "operations": sorted(operations)}
    try:
        status, body = fn(payload)
        if isinstance(body, dict) and body.get("run"):
            rdir, _ = studio_store.find_run(body["run"])
            if rdir:
                body["runDir"] = rdir
                for key, names in (("htmlPath", ("page.html",)),
                                   ("specPath", ("freeform.spec.json", "screenshot.spec.json", "pagespec.json")),
                                   ("reportPath", ("report.html", "acceptance.json"))):
                    body[key] = next((os.path.join(rdir, n) for n in names if os.path.isfile(os.path.join(rdir, n))), None)
                for item in body.get("files", []):
                    item["path"] = os.path.join(rdir, item["name"])
        return status, body
    except (KeyError, TypeError, ValueError) as exc:
        return 400, {"error": "Invalid request: %s" % exc}


def web_restore_operation(payload):
    import studio_server
    import web_routes
    return web_routes.restore(payload, studio_server.ai_conf(), studio_server._chat_prompt)


def spec_operation(payload):
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return 400, {"error": "spec must be an object"}
    if spec.get("kind") == "freeform":
        return ff.generate(payload)
    if studio_api._is_screenshot_spec(spec):
        return studio_api.run_screenshot_pipeline(spec, workspace=payload.get("workspace"))
    return studio_api.run_pipeline(spec, theme=payload.get("theme", "default"), workspace=payload.get("workspace"))


def zip_operation(payload):
    rid = payload.get("run")
    data, name = studio_store.zip_run(rid, deliverable_only=payload.get("deliverableOnly", True))
    if data is None:
        return 404, {"error": "Run not found"}
    # Default output remains within the same writable data root.
    export_dir = os.path.join(studio_store.DATA_ROOT, "exports")
    os.makedirs(export_dir, exist_ok=True)
    output = os.path.join(export_dir, name)
    with open(output, "wb") as handle:
        handle.write(data)
    return 200, {"ok": True, "path": output, "name": name, "size": len(data)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()
    with open(args.request, encoding="utf-8") as handle:
        request = json.load(handle)
    if not isinstance(request, dict):
        status, body = 400, {"error": "Request must be an object"}
    else:
        try:
            status, body = dispatch(request.get("operation"), request.get("payload", {}))
        except Exception as exc:
            # Infrastructure failure is still a structured bridge response.
            # Do not include environment values or raw adapter exceptions.
            status, body = 500, {"error": "Studio operation failed", "errorType": type(exc).__name__}
    os.makedirs(os.path.dirname(os.path.abspath(args.response)), exist_ok=True)
    with open(args.response, "w", encoding="utf-8") as handle:
        json.dump({"status": status, "body": body}, handle, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
