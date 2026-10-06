import pandas as pd
from forecast_engine import (
    PRODUCTS,
    backtest_forecast_methods,
    select_backtest_method,
    rolling_forecast_12m,
    latest_observed_period,
    period_index,
)


def make_sales(n=6):
    rows=[]
    vals=[100,120,95,140,180,220][:n]
    for i,v in enumerate(vals):
        rows.append({
            'periode': f'Année 1 - {['Janvier','Février','Mars','Avril','Mai','Juin'][i]}',
            'index': i,
            'Shorty 3': v,
            'Integral 3': v*2,
            'Shorty 5': v*0.5,
            'Integral 5': v*1.2,
            'Integral 7': v*0.8,
        })
    return pd.DataFrame(rows)


def factors():
    return {g: {i: 1.0 for i in range(12)} for g in (3,5,7)}


def test_backtest_uses_only_prior_observations():
    sales=make_sales(6)
    details, summary=backtest_forecast_methods(sales, factors(), min_train=2)
    assert not details.empty
    assert set(details['target_index']) == {2,3,4,5}
    # Aucun target ne peut être utilisé dans la construction de son cutoff.
    for _, r in details.iterrows():
        assert period_index(r['cutoff']) < int(r['target_index'])


def test_backtest_selects_a_valid_model():
    sales=make_sales(6)
    _, summary=backtest_forecast_methods(sales, factors(), min_train=2)
    method=select_backtest_method(summary)
    assert method in {'niveau saisonnier','tendance amortie + saisonnalité','Holt amorti + saisonnalité'}


def test_rolling_forecast_starts_after_anchor_and_has_12_future_months():
    sales=make_sales(6)
    forecast, results=rolling_forecast_12m(sales, factors(), 'Année 1 - Juin', method='niveau saisonnier', horizon=12)
    assert len(forecast)==13
    assert forecast.iloc[0]['periode']=='Année 1 - Juin'
    assert forecast.iloc[0]['statut']=='Réel'
    assert forecast.iloc[1]['periode']=='Année 1 - Juillet'
    assert forecast.iloc[-1]['periode']=='Année 2 - Juin'
    assert all(v>=0 for v in forecast[PRODUCTS].to_numpy().ravel())
    assert results['Shorty 3'].history_count==6


def test_latest_observed_period():
    sales=make_sales(6)
    assert latest_observed_period(sales)=='Année 1 - Juin'
