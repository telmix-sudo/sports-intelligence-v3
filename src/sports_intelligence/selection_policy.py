"""Explicit selection policies. A probability gap is not a win probability."""
import math

from .portfolio_controls import conservative_expected_value


def selection_trace(probabilities,market,prices,push_probability=0.,sample_min=None):
    if not math.isfinite(push_probability) or not 0<=push_probability<1:raise ValueError('Invalid push probability')
    n=len(probabilities)
    if n not in (2,3) or len(market)!=n or len(prices)!=n:raise ValueError('Inconsistent outcome vectors')
    if any(not math.isfinite(v) or not 0<=v<=1 for v in (*probabilities,*market)):raise ValueError('Invalid probability')
    if any(abs(sum(v)-1)>.001 for v in (probabilities,market)):raise ValueError('Invalid probability total')
    if any(not math.isfinite(v) or v<=1 for v in prices):raise ValueError('Invalid price')
    gap=max(range(n),key=lambda i:probabilities[i]-market[i])
    choices={'VALUE_GAP':gap,'MODEL_FAVOURITE':max(range(n),key=lambda i:probabilities[i]),
             'MARKET_FAVOURITE':max(range(n),key=lambda i:market[i])}
    if n==2:choices['OPPOSITE_VALUE_GAP']=1-gap
    trace = {'rule':'MAX_MODEL_MINUS_NO_VIG_MARKET','ties':'FIRST_QUOTED_OUTCOME',
            'selected_index':gap,'arms':{name:{'selection_index':i,'model_probability':probabilities[i],
            'market_probability':market[i],'odds':prices[i],'edge_pp':100*(probabilities[i]-market[i]),
            'model_expected_unit_pnl':(1-push_probability)*(probabilities[i]*prices[i]-1)} for name,i in choices.items()},
            'push_probability':push_probability,'probability_basis':'CONDITIONAL_ON_NO_PUSH' if push_probability else 'FULL_OUTCOME_SET',
            'opposite_available':n==2,'opposite_is_a_selection_policy_not_a_new_probability_model':True}
    if sample_min is not None:
        decisive = 1 - push_probability
        scores = [decisive * conservative_expected_value(p, price, sample_min)
                  for p, price in zip(probabilities, prices)]
        eligible = [i for i in range(n) if probabilities[i] - market[i] >= .04 and scores[i] >= .01]
        best = max(eligible or range(n), key=scores.__getitem__)
        trace['arms']['PRICE_ADJUSTED_VALUE'] = {
            'selection_index': best, 'model_probability': probabilities[best], 'market_probability': market[best],
            'odds': prices[best], 'edge_pp': 100 * (probabilities[best] - market[best]),
            'model_expected_unit_pnl': decisive * (probabilities[best] * prices[best] - 1),
            'conservative_ev': scores[best], 'passes_value_screen': bool(eligible) and sample_min >= 8,
        }
    return trace
