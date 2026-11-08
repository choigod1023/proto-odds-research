import importlib.util
import pathlib
import unittest
import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import state_model

spec = importlib.util.spec_from_file_location('experiment', pathlib.Path(__file__).with_name('experiment.py'))
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)

class TestEventHazard(unittest.TestCase):
    def test_current_game_events_do_not_change_prediction(self):
        state = {1:[10.,12.,8], 2:[12.,10.,8]}
        fixture = {'home_id':1,'away_id':2,'shots':[[1,15,0,.99,'Goal']], 'home_score':5,'away_score':0,'red_cards':[(2,4)]}
        a = experiment.forecast(fixture,state)
        fixture.update(shots=[[2,90,0,.01,'Goal']],home_score=0,away_score=9,red_cards=[(1,1)])
        self.assertEqual(a, experiment.forecast(fixture,state))
        self.assertAlmostEqual(sum(a),1)

    def test_sequence_is_time_dependent(self):
        close = [[1,2,0,.1,'Off T'],[1,4,0,.2,'Goal']]
        far = [[1,2,0,.1,'Off T'],[1,20,0,.2,'Goal']]
        self.assertEqual([x[2] for x in experiment.sequence(close,1)], [False,True])
        self.assertEqual([x[2] for x in experiment.sequence(far,1)], [False,False])

    def test_team_is_not_opponent(self):
        shots=[[2,1,0,.3,'Goal'],[1,2,0,.1,'Off T']]
        self.assertEqual(len(list(experiment.sequence(shots,1))),1)

    def test_simulation_marginalizes_current_events(self):
        state={1:[10.,12.,8], 2:[12.,10.,8]}
        params={'score_factors':[1.1,1.,.9], 'red_factor':.7, 'red_hazard':.001}
        fixture={'home_id':1,'away_id':2,'home_score':9,'red_cards':[(1,3)]}
        first=state_model.simulate(fixture,state,params,123,paths=100)
        fixture.update(home_score=0,red_cards=[(2,88)])
        self.assertEqual(first,state_model.simulate(fixture,state,params,123,paths=100))
        self.assertAlmostEqual(sum(first),1.)

if __name__ == '__main__': unittest.main()
