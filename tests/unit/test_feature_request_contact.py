"""FEATURE_REQUEST_CONTACT: whom to consult about features the assistant does not have. Both prompts name it
only when it is set in .env, so the public repository carries no person's name."""
import importlib

from config import settings


def _reload(monkeypatch, value):
    monkeypatch.setenv("FEATURE_REQUEST_CONTACT", value)
    return importlib.reload(settings)


def test_contact_is_named_in_both_prompts(monkeypatch):
    try:
        s = _reload(monkeypatch, "Mario Rossi, the project manager")
        assert "per le funzioni nuove bisogna consultare Mario Rossi, the project manager\n" in s.SYSTEM_PROMPT_IT
        assert "new features are a matter for Mario Rossi, the project manager\n" in s.SYSTEM_PROMPT_EN
    finally:
        monkeypatch.delenv("FEATURE_REQUEST_CONTACT")
        importlib.reload(settings)


def test_without_contact_the_feature_is_only_said_unavailable(monkeypatch):
    try:
        s = _reload(monkeypatch, "")
        assert "non c'è, senza promettere né escludere che arrivi\n" in s.SYSTEM_PROMPT_IT
        assert "per le funzioni nuove" not in s.SYSTEM_PROMPT_IT
        assert "without promising or ruling out that it will come\n" in s.SYSTEM_PROMPT_EN
    finally:
        monkeypatch.delenv("FEATURE_REQUEST_CONTACT")
        importlib.reload(settings)
