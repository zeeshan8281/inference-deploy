import json
from unittest.mock import patch

import httpx

from inference_deploy.cli import main


def test_registration_posts_json_and_reports_http_errors(tmp_path, capsys):
    source = tmp_path / "profile.json"
    source.write_text(json.dumps({"name": "candidate"}))
    response = httpx.Response(409, json={"detail": "operation in progress"})
    with patch("httpx.request", return_value=response) as request:
        assert main(["--url", "http://localhost:8088", "profile", str(source)]) == 1
    request.assert_called_once()
    assert request.call_args.args == ("POST", "http://localhost:8088/api/profiles")
    assert request.call_args.kwargs["json"] == {"name": "candidate"}
    assert "operation in progress" in capsys.readouterr().out
