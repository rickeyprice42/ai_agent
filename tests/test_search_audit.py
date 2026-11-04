from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_agent.tools.http import _plan_search,_rank_search_results

class SearchAuditTests(unittest.TestCase):
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
