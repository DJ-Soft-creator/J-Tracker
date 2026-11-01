"""Die Agentenmodule laden erst, wenn sie gebraucht werden.

Sie lagen als ausfuehrbare Scripts in beiden Shells und kosteten bei jedem
Start Parsezeit und eine Anfrage. Jetzt stehen sie als data-Bloecke
(`type="text/journl-lazy"`) in den Templates; `agent-lazy.js` injiziert die
echte Datei erst beim ersten Aufruf und stellt die synchron gerufenen Einstiege
als Stubs bereit.
"""

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]

LAZY_MODULES = (
    "provider-manager.js", "agent-viewport.js",
    "shopping-agent.js", "write-agent.js",
)


class AgentLazyTests(unittest.TestCase):
    def test_the_templates_declare_the_agent_modules_as_data_blocks(self):
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            with self.subTest(template=template_name):
                for module in LAZY_MODULES:
                    self.assertIn(f'type="text/journl-lazy" data-lazy="{module}"', template)
                self.assertIn("filename='agent-lazy.js'", template)
                # Der Loader muss nach allen data-Bloecken stehen, sonst findet
                # er ihre Quellen nicht.
                self.assertLess(
                    template.index("filename='write-agent.js'"),
                    template.index("filename='agent-lazy.js'"),
                )

    def test_the_loader_injects_on_demand_and_shims_the_sync_entry_points(self):
        loader = (ROOT_DIR / "app" / "static" / "agent-lazy.js").read_text(encoding="utf-8")
        self.assertIn('script[type="text/journl-lazy"][data-lazy]', loader)
        self.assertIn("window.journlAgentModules = { load }", loader)
        for entry in ("openWriteAgentFromDashboard", "openWriteAgentFromBrain", "openShoppingAgent"):
            self.assertIn(f"window.{entry} = function", loader)
        self.assertIn("'write-agent.js': ['agent-viewport.js']", loader)
        self.assertIn("'shopping-agent.js': ['agent-viewport.js']", loader)
        # Der Menueeintrag muss sichtbar sein, bevor das Modul geladen ist.
        self.assertIn("data-write-agent-entry", loader)
        self.assertNotIn("addWriteMenuEntry", (ROOT_DIR / "app" / "static" / "write-agent.js").read_text(encoding="utf-8"))

    def test_the_provider_page_loads_its_module_on_open(self):
        workbench = (ROOT_DIR / "app" / "static" / "agent-workbench.js").read_text(encoding="utf-8")
        self.assertIn("window.journlAgentModules.load('provider-manager.js')", workbench)


if __name__ == "__main__":
    unittest.main()
