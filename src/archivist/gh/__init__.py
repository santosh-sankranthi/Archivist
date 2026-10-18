"""GitHub adapter for the community agent (webhook + handlers).

All hosted-agent logic lives here; ``webapp.py`` only routes
``POST /github/webhook`` into :func:`archivist.gh.webhook.handle_webhook`.
"""
