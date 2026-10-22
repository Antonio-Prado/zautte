import json
import os

from crawler.state import write_json_atomic


def test_json_written_whole_and_mode_kept(tmp_path):
    p = tmp_path / "index.json"
    p.write_text("{}")
    os.chmod(p, 0o640)
    write_json_atomic(p, {"pages": [1, 2]}, indent=2)
    assert json.loads(p.read_text()) == {"pages": [1, 2]}
    assert oct(os.stat(p).st_mode & 0o777) == "0o640"
    assert not [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]


def test_new_file_is_readable_by_the_api_account(tmp_path):
    p = tmp_path / "pdf_link_texts.json"
    write_json_atomic(p, {"a": "b"})
    assert oct(os.stat(p).st_mode & 0o777) == "0o644"
