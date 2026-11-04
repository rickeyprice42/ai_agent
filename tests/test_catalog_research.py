from pathlib import Path
import json
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ai_agent.tools.browser import PlaywrightBrowserRunner, _catalog_resource_allowed
from ai_agent.tools.http import HttpSandbox, _plan_search, _rank_search_results
from ai_agent.tools.research import BrowserResearchReader, research_read_result
from tests.test_browser_research import FakeBrowser, FakeHttp, response


def title_offer(title, provider_id="tvs.sbd.4000", offer_type="TV_PLUS"):
    return {"$kind": "SuperheroLockup", "title": title, "type": "Movie", "brandId": provider_id,
            "buttons": [{"title": "Accept Free Trial", "availabilityText": "7 days free, then $14.99/month.",
                         "action": {"$kind": "commerceOfferAction", "modalParams": {"queryParams": {"brandId": provider_id}, "data": {"type": offer_type}}}}]}


class CatalogResearchTests(unittest.TestCase):
    def test_book_extracts_item_author_currency_and_price_from_json_ld(self):
        ld = {"@context": "https://schema.org", "@type": "Book", "name": "Example Book", "author": {"@type": "Person", "name": "Actual Author"}, "offers": {"@type": "Offer", "price": "4.99", "priceCurrency": "USD"}}
        body = '<script type="application/ld+json">{"@type":"Organization","name":"Apple Books"}</script><script type="application/ld+json">' + json.dumps(ld) + '</script><main>' + 'Book description. ' * 20 + '</main>'
        result = BrowserResearchReader(FakeHttp(response(body, url="https://books.apple.com/us/book/example/id123")), FakeBrowser()).read(url="https://books.apple.com/us/book/example/id123")
        item = result["fields"]["catalog_item"]
        self.assertEqual(item["authors"], ["Actual Author"])
        self.assertEqual(item["offers"][0]["priceCurrency"], "USD")
        self.assertEqual(item["offers"][0]["price"], "4.99")
        self.assertEqual(result["evidence"][0]["source"], "catalog_item")

    def test_current_apple_books_string_author_and_numeric_offer(self):
        # Minimal public schema shape observed from the actual Apple Books page;
        # no live-network dependency or copied publisher description.
        ld = {"@type": "Book", "name": "Parser fixture", "author": "Fixture Author", "offers": {"@type": "Offer", "price": 59.99, "priceCurrency": "USD"}}
        body = '<script type="application/ld+json">' + json.dumps(ld) + '</script><main>$59.99 Publisher Description. ' + 'Readable public book content. ' * 10 + '</main>'
        item = BrowserResearchReader(FakeHttp(response(body, url="https://books.apple.com/us/book/fixture/id1")), FakeBrowser()).read(url="https://books.apple.com/us/book/fixture/id1")["fields"]["catalog_item"]
        self.assertEqual(item["authors"], ["Fixture Author"])
        self.assertEqual(item["offers"], [{"price": 59.99, "priceCurrency": "USD"}])

    def test_book_title_and_coauthor_entities_are_decoded(self):
        ld = {"@type": "Book", "name": "A &amp; B", "author": "First Author &amp; Second Author", "offers": {"price": 13.99, "priceCurrency": "USD"}}
        body = '<script type="application/ld+json">' + json.dumps(ld) + '</script><main>' + 'Readable book details. ' * 20 + '</main>'
        item = BrowserResearchReader(FakeHttp(response(body, url="https://books.apple.com/us/book/fixture/id1")), FakeBrowser()).read(url="https://books.apple.com/us/book/fixture/id1")["fields"]["catalog_item"]
        self.assertEqual(item["title"], "A & B")
        self.assertEqual(item["authors"], ["First Author & Second Author"])

    def test_title_subscription_offer_does_not_require_obsolete_tv_plus_spelling(self):
        body = '<title>Watch Movie - Apple TV</title><script type="application/json">' + json.dumps(title_offer("Movie")) + '</script><main>Movie. 7 days free, then $14.99/month. ' + 'Actual movie description. ' * 20 + '</main>'
        result = BrowserResearchReader(FakeHttp(response(body, url="https://tv.apple.com/us/movie/example/umc.cmc.1")), FakeBrowser()).read(url="https://tv.apple.com/us/movie/example/umc.cmc.1", catalog="apple_tv_plus")
        self.assertEqual(result["fields"]["catalog_item"]["access"], "subscription")
        self.assertEqual(result["fields"]["catalog_item"]["provider_id"], "tvs.sbd.4000")

    def test_other_channel_trial_and_global_promotion_are_not_apple_subscription(self):
        url = "https://tv.apple.com/us/movie/rental/umc.cmc.2"
        for payload in [title_offer("Rental", "tvs.sbd.starz", "CHANNEL"), {"tabs": [title_offer("Different movie")]}]:
            with self.subTest(payload=payload):
                body = '<title>Rental - Apple TV</title><main>STARZ. 7 days free, then $9.99/month. Buy or rent this movie. ' + 'Actual rental movie description. ' * 20 + '</main><script type="application/json">' + json.dumps(payload) + '</script>'
                result = research_read_result(BrowserResearchReader(FakeHttp(response(body, url=url)), FakeBrowser()), {"url": url, "catalog": "apple_tv_plus"})
                self.assertEqual(result.error.code, "availability_unconfirmed")

    def test_browser_snapshot_keeps_only_title_bound_public_offer_facts(self):
        class CatalogPage:
            url = "https://tv.apple.com/us/movie/movie/umc.cmc.1"
            def evaluate(self, *_args):
                return {"mainText": "Movie trial", "elements": [], "jsonLd": [{"@type": "Movie", "name": "Movie"}]}
            def content(self):
                return '<title>Movie</title><script type="application/json">' + json.dumps({"data": title_offer("Movie"), "userTokenHash": "must-never-leave-runner"}) + '</script>'
        with tempfile.TemporaryDirectory() as directory:
            snapshot = PlaywrightBrowserRunner(Path(directory))._snapshot(CatalogPage())
        self.assertEqual(snapshot["catalog_item"]["provider_id"], "tvs.sbd.4000")
        self.assertNotIn("must-never-leave-runner", json.dumps(snapshot))
        self.assertNotIn("userTokenHash", json.dumps(snapshot))

    def test_brand_footer_alone_never_proves_item_subscription(self):
        body = '<title>Rental</title><main>Rental. Buy $9.99 or rent $3.99. ' + 'Movie description. ' * 20 + '</main><footer>Apple TV+ subscription available</footer>'
        result = research_read_result(BrowserResearchReader(FakeHttp(response(body, url="https://tv.apple.com/us/movie/rental/umc.cmc.2")), FakeBrowser()), {"url": "https://tv.apple.com/us/movie/rental/umc.cmc.2", "catalog": "apple_tv_plus"})
        self.assertEqual(result.error.code, "availability_unconfirmed")

    def test_public_sign_in_navigation_is_not_misclassified_as_login_gate(self):
        body = '<title>Public book</title><main>Sign in. Public title description ' + 'Readable book facts. ' * 12 + '</main>'
        result = BrowserResearchReader(FakeHttp(response(body, url="https://books.apple.com/us/book/example/id123")), FakeBrowser()).read(url="https://books.apple.com/us/book/example/id123")
        self.assertEqual(result["status"], 200)

    def test_http_title_without_offers_can_hydrate_subscription_in_browser(self):
        url = "https://tv.apple.com/us/movie/example/umc.cmc.1"
        http = FakeHttp(response('<main>' + 'Movie description. ' * 20 + '</main>', url=url))
        browser = FakeBrowser({"url": url, "title": "Movie", "status": 200, "main_text": 'Movie. 7 days free, then $14.99/month. ' + 'Description. ' * 20, "catalog_item": {"title": "Movie", "url": url, "region": "us", "access": "subscription", "provider": "Apple TV", "provider_id": "tvs.sbd.4000", "offer_type": "TV_PLUS"}})
        result = BrowserResearchReader(http, browser).read(url=url, catalog="apple_tv_plus")
        self.assertEqual(result["extraction_method"], "browser")
        self.assertEqual(result["fields"]["catalog_item"]["access"], "subscription")

    def test_storefront_redirect_is_not_claimed_as_requested_region(self):
        result = research_read_result(BrowserResearchReader(FakeHttp(response('<main>' + 'Book description. ' * 20 + '</main>', url="https://books.apple.com/gb/book/example/id123")), FakeBrowser()), {"url": "https://books.apple.com/us/book/example/id123", "region": "us"})
        self.assertEqual(result.error.code, "region_mismatch")

    def test_read_document_cap_is_bounded_separately_from_short_http_tool(self):
        original = HttpSandbox(max_response_chars=20000)
        reader = BrowserResearchReader(original, FakeBrowser())
        self.assertEqual(original.max_response_chars, 20000)
        self.assertEqual(reader.http.max_response_chars, 2000000)

    def test_browser_cdn_rules_do_not_expand_navigation_or_writes(self):
        for host in ("js-cdn.music.apple.com.evil.test", "evil.test", "mzstatic.com.evil.test"):
            self.assertFalse(_catalog_resource_allowed("tv.apple.com", host, "GET", False))
        self.assertTrue(_catalog_resource_allowed("tv.apple.com", "is1-ssl.mzstatic.com", "GET", False))
        self.assertFalse(_catalog_resource_allowed("example.com", "is1-ssl.mzstatic.com", "GET", False))
        self.assertFalse(_catalog_resource_allowed("tv.apple.com", "is1-ssl.mzstatic.com", "POST", False))
        self.assertFalse(_catalog_resource_allowed("tv.apple.com", "is1-ssl.mzstatic.com", "GET", True))

    def test_search_discovery_preserves_unverified_title_for_actual_page_verification(self):
        plan = _plan_search("Apple TV subscription crime", "us")
        accepted, _ = _rank_search_results(plan, [{"url": "https://tv.apple.com/us/movie/crime/umc.cmc.1", "title": "Crime", "snippet": "A crime movie."}], 3)
        self.assertEqual(len(accepted), 1)
        plan = _plan_search("wolfs site:tv.apple.com/gb/movie")
        self.assertEqual(plan.storefront, "gb")
        self.assertNotIn("site", plan.subject_terms)


if __name__ == "__main__":
    unittest.main()
