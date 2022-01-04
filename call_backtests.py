# -*- coding: utf-8 -*-
from datetime import datetime
import time

from strategies import netzero, the_bull, bf70
import backtest_strategies
import compute_stats
import os
from util import underlying_util

start_time = time.time()
start = datetime(2006, 11, 1).date()
end = datetime(2021, 12, 31).date()

frequency_string = 'm'
quantity = None
risk_capital = 100000
include_underlying = True
underlying = "^RUT"
path = os.getcwd()


def concat_strategy_name(strategy):
  strategy_name = strategy + '_' + \
      underlying.replace('^', '').lower() + '_' + frequency_string
  if quantity is not None:
    strategy_name += "_q" + str(quantity)
  strategy_name += "_" + str(start)
  return strategy_name


bf70_name = concat_strategy_name('bf70')
backtest_strategies.backtest(bf70.bf70(), underlying, bf70_name, risk_capital,
                             quantity, start, end, bf70.parameters, frequency_string, include_underlying)
if include_underlying:
  underlying_util.add_underlying(
      start, end, underlying, risk_capital, bf70_name)
compute_stats.compute_stats(
    bf70_name, underlying, risk_capital, [])


the_bull_name = concat_strategy_name('the_bull')
backtest_strategies.backtest(the_bull.bull(), underlying, the_bull_name, risk_capital,
                             quantity, start, end, the_bull.parameters, frequency_string, include_underlying)
if include_underlying:
  underlying_util.add_underlying(start, end, underlying,
                                 risk_capital, the_bull_name)
compute_stats.compute_stats(
    the_bull_name, underlying, risk_capital, [])


netzero_name = concat_strategy_name('netzero')
backtest_strategies.backtest(netzero.netzero(), underlying, netzero_name, risk_capital,
                             quantity, start, end, netzero.parameters, frequency_string, include_underlying)
if include_underlying:
  underlying_util.add_underlying(start, end, underlying,
                                 risk_capital, netzero_name)
compute_stats.compute_stats(
    netzero_name, underlying, risk_capital, [])

print()
print("--- %s seconds ---" % (time.time() - start_time))
