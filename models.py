from otree.api import (
    models, BaseConstants, BaseSubsession, BasePlayer
)

from django.contrib.contenttypes.models import ContentType
from otree_redwood.models import Event, DecisionGroup

import csv
import random
import math
import otree.common

doc = """
This is a configurable bimatrix game.
"""


class Constants(BaseConstants):
    name_in_url = 'bimatrix'
    # players per group when not using mean matching
    players_per_group = 2
	# Maximum number of rounds, actual number is taken as the max round
	# in the config file.
    num_rounds = 100
    base_points = 0
    


def parse_config(config_file):
    with open('bimatrix/configs/' + config_file) as f:
        rows = list(csv.DictReader(f))

    rounds = []
    for row in rows:
        rounds.append({
            'shuffle_role': True if row['shuffle_role'] == 'TRUE' else False,
            'period_length': int(row['period_length']),
            'num_subperiods': int(row['num_subperiods']),
            'pure_strategy': True if row['pure_strategy'] == 'TRUE' else False,
            'show_at_worst': True if row['show_at_worst'] == 'TRUE' else False,
            'show_best_response': True if row['show_best_response'] == 'TRUE' else False,
            'rate_limit': int(row['rate_limit']) if row['rate_limit'] else 0,
            'mean_matching': True if row['mean_matching'] == 'TRUE' else False,
            'payoff_matrix': [
                [int(row['payoff1Aa']), int(row['payoff2Aa'])], [int(row['payoff1Ab']), int(row['payoff2Ab'])],
                [int(row['payoff1Ba']), int(row['payoff2Ba'])], [int(row['payoff1Bb']), int(row['payoff2Bb'])]
            ],
        })
    return rounds


class Subsession(BaseSubsession):

    def creating_session(self):
        config = self.config
        if not config:
            return

        #Random round picked for payment
        self.session.vars['payment_round'] = random.randint(4, self.num_rounds())
        
        num_silos = self.session.config['num_silos']
        fixed_id_in_group = not config['shuffle_role']

        players = self.get_players()
        num_players = len(players)
        silos = [[] for _ in range(num_silos)]
        for i, player in enumerate(players):
            if self.round_number == 1:
                player.silo_num = math.floor(num_silos * i/num_players)
            else:
                player.silo_num = player.in_round(1).silo_num
            silos[player.silo_num].append(player)

        group_matrix = []
        for silo in silos:
            if config['mean_matching']:
                silo_matrix = [ silo ]
            else:
                silo_matrix = []
                ppg = Constants.players_per_group
                for i in range(0, len(silo), ppg):
                    silo_matrix.append(silo[i:i+ppg])
            group_matrix.extend(otree.common._group_randomly(silo_matrix, fixed_id_in_group))

        self.set_group_matrix(group_matrix)
    
    def set_initial_decisions(self):
        pure_strategy = self.config['pure_strategy']
        for player in self.get_players():
            if pure_strategy:
                player._initial_decision = random.choice([0, 1])
            else:
                player._initial_decision = random.random()

    def num_rounds(self):
        return len(parse_config(self.session.config['config_file']) )

    @property
    def config(self):
        try:
            return parse_config(self.session.config['config_file'])[self.round_number-1]
        except IndexError:
            return None


class Group(DecisionGroup):

    def num_subperiods(self):
        return self.subsession.config['num_subperiods']

    def period_length(self):
        return self.subsession.config['period_length']
    
    def rate_limit(self):
        config = self.subsession.config
        if not config['pure_strategy'] and config['mean_matching']:
            return 0.2
        else:
            return None

    def set_payoffs(self):
        period_start = self.get_start_time()
        period_end = self.get_end_time()
        if None in (period_start, period_end):
            print('cannot set payoff, period has not ended yet')
            return
        decisions = self.get_group_decisions_events()
        payoff_matrix = self.subsession.config['payoff_matrix']
        for player in self.get_players():
            player.set_payoff(period_start, period_end, decisions, payoff_matrix)


class Player(BasePlayer):

    silo_num = models.IntegerField()
    _initial_decision = models.FloatField()
    final_payoff = models.CurrencyField()

    def initial_decision(self):
        return self._initial_decision

    def role(self):
        if self.id_in_group % 2 == 0:
            return 'column'
        else:
            return 'row'


    def get_average_strategy(self, period_start, period_end, decisions):
        weighted_sum_decision = 0
        while decisions:
            cur_decision = decisions.pop(0)
            next_change_time = decisions[0].timestamp if decisions else period_end
            decision_value = cur_decision.value[self.participant.code]
            weighted_sum_decision += decision_value * (next_change_time - cur_decision.timestamp).total_seconds()
        return weighted_sum_decision / self.group.period_length()

    #get frequency of player picking choice
    def get_frequency(self, choice, decisions):
        count = 0
        total = 0
        decisions = self.group.get_group_decisions_events()
        while decisions:
            cur_decision = decisions.pop(0)
            decision_value = cur_decision.value[self.participant.code]
            total += 1
            if (decision_value == choice):
                count += 1
        return count / total
        

    def set_payoff(self, period_start, period_end, decisions, payoff_matrix):
        period_duration = period_end - period_start

        payoff = 0
        role_index = 0 if self.role() == 'row' else 1

        Aa = payoff_matrix[0][role_index]
        Ab = payoff_matrix[1][role_index]
        Ba = payoff_matrix[2][role_index]
        Bb = payoff_matrix[3][role_index]

        q1, q2 = 0.5, 0.5
        for i, d in enumerate(decisions):
            if not d.value: continue

            other_role_decisions = [d.value[p.participant.code] for p in self.group.get_players() if p.role() != self.role()]
            if self.role() == 'row':
                q1 = d.value[self.participant.code]
                q2 = sum(other_role_decisions) / len(other_role_decisions)
            else:
                q2 = d.value[self.participant.code]
                q1 = sum(other_role_decisions) / len(other_role_decisions)

            flow_payoff = ((Aa * q1 * q2) +
                           (Ab * q1 * (1 - q2)) +
                           (Ba * (1 - q1) * q2) +
                           (Bb * (1 - q1) * (1 - q2)))

            if self.group.num_subperiods():
                if i == 0:
                    prev_change_time = period_start
                else:
                    prev_change_time = decisions[i - 1].timestamp
                decision_length = (d.timestamp - prev_change_time).total_seconds()
            else:
                if i + 1 < len(decisions):
                    next_change_time = decisions[i + 1].timestamp
                else:
                    next_change_time = period_end
                decision_length = (next_change_time - d.timestamp).total_seconds()
            payoff += decision_length * flow_payoff

        self.payoff = payoff / period_duration.total_seconds()    
        if self.round_number == self.subsession.num_rounds():
            self.final_payoff = self.in_round(self.session.vars['payment_round']).payoff
