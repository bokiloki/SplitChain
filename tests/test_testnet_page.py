from html.parser import HTMLParser
from pathlib import Path


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = set()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "a" and "href" in values:
            self.targets.add(values["href"])


def test_landing_page_links_have_routes_and_anchor_targets():
    page = Path(__file__).parents[1] / "splitchain/web/testnet.html"
    parser = Links()
    parser.feed(page.read_text(encoding="utf-8"))
    internal = {"/", "/genesis.json", "/.well-known/splitchain-testnet.json",
                "/status", "/leadership"}
    assert {link for link in parser.targets if link.startswith("/")} <= internal
    assert {link.removeprefix("#") for link in parser.targets if link.startswith("#")} <= parser.ids
    assert all(link.startswith("https://github.com/bokiloki/SplitChain")
               for link in parser.targets if link.startswith("http"))
