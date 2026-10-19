"""Plugin tools can declare ``end_turn=True``; the registry keeps the flag.

Split out of ``tests/hermes_cli/test_plugins.py`` (code-health size ratchet).
"""

import hermes_yaml as yaml

from hermes_cli.plugins import PluginManager


def test_register_tool_preserves_end_turn_flag(tmp_path, monkeypatch):
    """Plugin tools can declare that they legitimately end the turn."""
    from tools.registry import registry

    plugins_dir = tmp_path / "hermes_test" / "plugins"
    plugin_dir = plugins_dir / "terminal_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(yaml.safe_dump({"name": "terminal_plugin"}))
    (plugin_dir / "__init__.py").write_text(
        'def register(ctx):\n'
        '    ctx.register_tool(\n'
        '        name="terminal_tool",\n'
        '        toolset="plugin_terminal_plugin",\n'
        '        schema={"name": "terminal_tool", "description": "Ends turn", "parameters": {"type": "object", "properties": {}}},\n'
        '        handler=lambda args, **kw: "ok",\n'
        '        end_turn=True,\n'
        '    )\n'
    )
    hermes_home = tmp_path / "hermes_test"
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["terminal_plugin"]}})
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    mgr = PluginManager()
    mgr.discover_and_load()

    assert registry.is_end_turn("terminal_tool") is True
