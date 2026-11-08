from pathlib import Path
import sys
import unittest
from io import BytesIO
from urllib import parse
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_agent.tools.http import HttpSandbox,HttpTransportError,_decode_body,_plan_search,_rank_search_results

class SearchResponse(BytesIO):
    headers={'Content-Type':'text/html; charset=utf-8'}


class SearchAuditTests(unittest.TestCase):
    def test_utf8_byte_cap_and_compressed_body_are_honestly_bounded(self):
        import gzip
        response=SearchResponse(('я'*20).encode())
        response.status=200
        response.getcode=lambda:200
        response.geturl=lambda:'https://example.com/data'
        with patch.object(HttpSandbox,'_validate_url',return_value=parse.urlparse('https://example.com/data')),patch('ai_agent.tools.http.request.build_opener') as opener:
            opener.return_value.open.return_value=response
            result=HttpSandbox(max_response_chars=10).fetch('https://example.com/data')
        self.assertTrue(result['truncated'])
        text,truncated=_decode_body(gzip.compress(b'x'*1000000),1000,{'Content-Encoding':'gzip'})
        self.assertEqual(len(text),1000)
        self.assertTrue(truncated)

    def test_direct_ssl_exception_is_typed_as_tls_not_private_network(self):
        import ssl
        with patch.object(HttpSandbox,'_validate_url',return_value=parse.urlparse('https://example.com/data')),patch('ai_agent.tools.http.request.build_opener') as opener:
            opener.return_value.open.side_effect=ssl.SSLCertVerificationError('unable to get local issuer certificate')
            with self.assertRaises(HttpTransportError) as error:
                HttpSandbox().fetch('https://example.com/data')
            self.assertEqual(error.exception.code,'tls_error')

    def test_lite_discovery_is_primary_and_preserves_direct_result_url(self):
        href='//duckduckgo.com/l/?'+parse.urlencode({'uddg':'https://arxiv.org/abs/2505.09388'})
        html=f'<a class="result-link" href="{href}">Qwen3 Technical Report</a><td class="result-snippet">A multilingual Qwen3 tool calling study.</td>'
        with patch('ai_agent.tools.http.request.urlopen',return_value=SearchResponse(html.encode())) as call:
            result=HttpSandbox().search('Qwen3 technical report multilingual tool calling 2025')
            self.assertEqual(call.call_count,1)
            self.assertIn('lite.duckduckgo.com',call.call_args.args[0].full_url)
            self.assertIn('Search provider: duckduckgo_lite',result)
            self.assertIn('https://arxiv.org/abs/2505.09388',result)
            self.assertIn('discovery snippets only',result)

    def test_lite_challenge_is_not_executed_and_fallback_is_bounded_with_cooldown(self):
        rss=b'<rss><channel><item><title>Qwen3 Technical Report</title><link>https://arxiv.org/abs/2505.09388</link><description>Qwen3 multilingual tool calling.</description></item></channel></rss>'
        sandbox=HttpSandbox()
        with patch('ai_agent.tools.http.request.urlopen',side_effect=[SearchResponse(b'<form id="challenge-form">Verify you are human</form>'),SearchResponse(rss),SearchResponse(rss)]) as call:
            self.assertIn('bing_rss',sandbox.search('Qwen3 technical report'))
            self.assertIn('bing_rss',sandbox.search('Qwen3 multilingual study'))
            self.assertEqual(call.call_count,3)
            self.assertIn('bing.com',call.call_args.args[0].full_url)

    def test_exact_model_repository_is_discoverable_without_every_query_facet_in_snippet(self):
        plan=_plan_search('Qwen3 technical report multilingual tool calling 2025')
        repo={'title':'GitHub - QwenLM/Qwen3: Qwen3 is the large language model series',
              'url':'https://github.com/QwenLM/Qwen3','snippet':'Weights and open dense and Mixture-of-Expert models.'}
        noise={'title':'University of California, Berkeley','url':'https://www.berkeley.edu/','snippet':'Our degree programs and research.'}
        accepted,_=_rank_search_results(plan,[repo,noise],5)
        self.assertEqual(accepted,[repo])

    def test_unquoted_short_version_hint_does_not_hide_relevant_leaderboard_source(self):
        plan=_plan_search('Berkeley function calling leaderboard BFCL V4 2025')
        candidate={'title':'Berkeley Function Calling Leaderboard','url':'https://gorilla.cs.berkeley.edu/leaderboard.html',
                   'snippet':'BFCL evaluates function calling capabilities.'}
        accepted,_=_rank_search_results(plan,[candidate],5)
        self.assertEqual(accepted,[candidate])
        self.assertNotIn('V4',plan.identity_terms)

    def test_domain_query_does_not_accept_dutch_television_noise(self):
        plan=_plan_search('tv.apple.com/us/movie wolfs')
        self.assertEqual(plan.intent,'apple_tv_catalog')
        accepted,rejected=_rank_search_results(plan,[{'title':'KPN TV','url':'https://tv.kpn.com/tv-gids','snippet':'TV gids en televisie'},{'title':'Wolfs','url':'https://tv.apple.com/us/movie/wolfs/umc.cmc.123','snippet':'Wolfs movie'}],5)
        self.assertEqual(len(accepted),1)
        self.assertEqual(rejected['wrong_catalog'],1)

    def test_explicit_site_filter_rejects_spoofed_and_other_domains(self):
        plan=_plan_search('dataclasses site:docs.python.org')
        candidates=[{'title':'dataclasses','url':url,'snippet':'Python dataclasses documentation'} for url in ['https://docs.python.org/3/library/dataclasses.html','https://docs.python.org.evil.test/dataclasses','https://example.com/dataclasses']]
        accepted,rejected=_rank_search_results(plan,candidates,5)
        self.assertEqual([r['url'] for r in accepted],[candidates[0]['url']])
        self.assertEqual(rejected['domain_mismatch'],2)

    def test_price_discovery_keeps_matching_product_without_a_price_snippet(self):
        candidate = {'title':'Tissot Everytime', 'url':'https://example.com/tissot/everytime', 'snippet':'Specifications and purchase options.'}
        for query in ['Tissot Everytime watch price', 'How much does Tissot Everytime cost?', 'Сколько стоят часы Tissot Everytime?']:
            with self.subTest(query=query):
                plan = _plan_search(query)
                accepted, rejected = _rank_search_results(plan, [candidate], 5)
                self.assertEqual(accepted, [candidate])
                self.assertEqual(rejected['price_not_verified'], 0)

    def test_marketplace_and_price_cues_do_not_match_another_product(self):
        for query in ['Chrono24 Tissot Everytime price', 'chrono24 tissot everytime price']:
            with self.subTest(query=query):
                plan = _plan_search(query)
                wrong = [
                    {'title':'Rolex prices on Chrono24', 'url':'https://www.chrono24.nl/rolex/index.htm', 'snippet':'Rolex watches from EUR 1000 on Chrono24.'},
                    {'title':'Breitling prices on Chrono24', 'url':'https://www.chrono24.nl/breitling/index.htm', 'snippet':'Breitling watches from EUR 5000 on Chrono24.'},
                ]
                accepted, _ = _rank_search_results(plan, wrong, 5)
                self.assertEqual(accepted, [])

    def test_explicit_reference_and_quoted_series_are_preserved(self):
        plan = _plan_search('CASIO "MTP-V002" watch price')
        candidates = [
            {'title':'Casio G-Shock DW-5600','url':'https://example.com/casio/dw5600','snippet':'Casio digital watch $99'},
            {'title':'Casio MTP V002','url':'https://example.com/casio/mtpv002','snippet':'Product specifications.'},
        ]
        accepted, rejected = _rank_search_results(plan, candidates, 5)
        self.assertEqual(accepted, [candidates[1]])
        self.assertEqual(rejected['identity_mismatch'], 1)

    def test_one_franchise_token_does_not_match_a_different_game(self):
        plan = _plan_search('GTA San Andreas ped ID list all models wiki')
        wrong = {'title':'Grand Theft Auto V - Rockstar Games', 'url':'https://www.rockstargames.com/gta-v/',
                 'snippet':'All GTA Online upgrades and expansions since launch.'}
        correct = {'title':'GTA San Andreas skin IDs', 'url':'https://example.com/gta-san-andreas/skins',
                   'snippet':'Pedestrian model list for San Andreas.'}
        accepted, _ = _rank_search_results(plan, [wrong, correct], 5)
        self.assertEqual(accepted, [correct])

    def test_quoted_or_group_accepts_either_phrase_not_both(self):
        plan = _plan_search('"GTA San Andreas" "idlewood" OR "ballas" skin list')
        candidates = [
            {'title':'GTA San Andreas Ballas skins', 'url':'https://example.com/ballas', 'snippet':'Pedestrian models and skins.'},
            {'title':'GTA San Andreas Idlewood skins', 'url':'https://example.com/idlewood', 'snippet':'Pedestrian models and skins.'},
            {'title':'GTA San Andreas unrelated skin', 'url':'https://example.com/unrelated', 'snippet':'Other pedestrian models.'},
        ]
        accepted, rejected = _rank_search_results(plan, candidates, 5)
        self.assertEqual({item['url'] for item in accepted}, {item['url'] for item in candidates[:2]})
        self.assertEqual(rejected['identity_mismatch'], 1)

if __name__=='__main__':unittest.main()
