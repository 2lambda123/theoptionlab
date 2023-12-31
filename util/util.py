import zipfile
from scipy.interpolate import InterpolatedUnivariateSpline as interpol
from py_vollib import black_scholes
from private import settings
from util import postgresql_connector

from datetime import datetime, time, timedelta, date
import math
import pandas as pd
import numpy as np
import scipy.stats as st
import os

import pandas_market_calendars as pmc
import pytz
xnys = pmc.get_calendar("XNYS")


def dateparse(x): return datetime.strptime(x, '%d.%m.%Y')


years = ([0.0, 1 / 360, 1 / 52, 1 / 12, 2 / 12, 3 / 12, 6 / 12, 12 / 12])
functions_dict = {}

df_yields = pd.read_csv(settings.path_to_libor_csv, index_col='Date', parse_dates=[
                        'Date'], date_parser=dateparse)
df_yields = df_yields.drop(['Week day'], axis=1)


entries = []

ratio = 100
dividend = 0
commissions = 1.25
connector = postgresql_connector.MyDB()

interest = 0.0225
yeartradingdays = 252

min_value = 1
max_value = 100000

printalot = True

valid_days = xnys.valid_days(
    start_date='2004-01-1', end_date=date.today(), tz='America/New_York')

startdates = {
    "^RUT": datetime(2004, 1, 2).date(),
    "^SPX": datetime(2004, 1, 2).date(),
    "^VIX": datetime(2006, 2, 27).date(),
    "SPLV": datetime(2011, 11, 1).date(),
    "SPHB": datetime(2013, 7, 24).date(),
    "VXX": datetime(2010, 5, 28).date()
}


class Strategy(object):

  def setParameters(self, permutation):

    self.patient_days_before = permutation['patient_days_before']
    self.patient_days_after = permutation['patient_days_after']
    self.cheap_entry = permutation['cheap_entry']
    self.down_day_entry = permutation['down_day_entry']
    self.patient_entry = permutation['patient_entry']
    self.min_vix_entry = permutation['min_vix_entry']
    self.max_vix_entry = permutation['max_vix_entry']
    self.min_iv_entry = permutation['min_iv_entry']
    self.max_iv_entry = permutation['max_iv_entry']
    self.sma_window = permutation['sma_window']
    self.dte_entry = permutation['dte_entry']
    self.els_entry = permutation['els_entry']
    self.ew_exit = permutation['ew_exit']
    self.pct_exit = permutation['pct_exit']
    self.dte_exit = permutation['dte_exit']
    self.dit_exit = permutation['dit_exit']
    self.deltatheta_exit = permutation['deltatheta_exit']
    self.tp_exit = permutation['tp_exit']
    self.sl_exit = permutation['sl_exit']
    self.delta = permutation['delta']

  def checkEntry(self, underlying, current_date):
    return True

  def checkCombo(self, underlying, combo):
    return True

  def adjust(self, underying, combo, current_date, realized_pnl, entry_price, expiration, position_size, dte, rh):
    return combo, realized_pnl, rh

  def checkExit(self):
    return False


def derive_strategy_code(permutation, parameters):
  strategy_code = ''

  if permutation['cheap_entry'] is not None:
    strategy_code += 'C'
  if ((len(parameters['down_day_entry']) > 1) and permutation['down_day_entry']):
    strategy_code += 'D'
  if ((len(parameters['patient_entry']) > 1) and permutation['patient_entry']):
    strategy_code += 'P'
  if ((len(parameters['ew_exit']) > 1) and permutation['ew_exit']):
    strategy_code += 'E'
  if (len(parameters['min_vix_entry']) > 1):
    strategy_code += '_X' + str(permutation['min_vix_entry'])
  if permutation['max_vix_entry'] is not None:
    strategy_code += '_X<_' + str(permutation['max_vix_entry'])
  if (len(parameters['min_iv_entry']) > 1):
    strategy_code += '_I' + \
        ('None' if permutation['min_iv_entry'] is None else str(
            int(permutation['min_iv_entry'] * 100)))
  if permutation['max_iv_entry'] is not None:
    strategy_code += '_I' + str(int(permutation['max_iv_entry'] * 100))
  if permutation['sma_window'] is not None:
    strategy_code += '_A' + str(permutation['sma_window'])
  if (len(parameters['dte_entry']) > 1):
    strategy_code += '_E' + str(permutation['dte_entry'])
  if permutation['els_entry'] is not None:
    strategy_code += '_EE' + str(permutation['els_entry'])
  if ((len(parameters['pct_exit']) > 1) and permutation['pct_exit'] is not None):
    strategy_code += '_C' + str(int(permutation['pct_exit'] * 100))
  if ((len(parameters['dte_exit']) > 1) and permutation['dte_exit'] != 0):
    strategy_code += '_X' + str(permutation['dte_exit'])
  if ((len(parameters['dit_exit']) > 1) and permutation['dit_exit'] != 0):
    strategy_code += '_EXDIT' + str(permutation['dit_exit'])
  if (len(parameters['deltatheta_exit']) > 1 and permutation['deltatheta_exit'] is not None):
    strategy_code += '_DT' + str(int(permutation['deltatheta_exit'] * 100))
  code_tp = permutation['tp_exit']
  if (code_tp is not None) and code_tp < 1:
    code_tp = int(code_tp * 100)
  if (len(parameters['tp_exit']) > 1):
    strategy_code += '_P' + str(code_tp)
  if (len(parameters['sl_exit']) > 1):
    strategy_code += '_L' + str(permutation['sl_exit'])
  if (len(parameters['delta']) > 1):
    strategy_code += '_D_' + str(permutation['delta'])

  if strategy_code == '':
    strategy_code = 'X'
  if strategy_code.startswith('_'):
    strategy_code = strategy_code[1:]
  strategy_code = strategy_code.replace('_None', 'X')
  strategy_code = strategy_code.replace('None', 'X')
  strategy_code = strategy_code.replace('.', 'x')
  return (strategy_code)


class Option():

  def __init__(self, entry_date, underlying, strike, expiration, sort):

    result = connector.check_option(underlying, strike, entry_date, expiration)

    if not (result == 1):
      raise ValueError('Option not in DB')

    self.underlying = underlying
    self.strike = strike
    self.expiration = expiration
    self.entry_date = entry_date
    self.type = sort


class Position():

  def __init__(self, option, entry_price, amount):
    self.option = option
    self.entry_price = entry_price
    self.amount = amount


class Combo(object):

  def __init__(self, positions):
    self.positions = positions

  def getPositions(self):
    return self.positions

  def getMinExpiration(self):

    el = getExpirationCombo(self)

    if ((0 in el.values()) or None in el.values()):
      return None

    return (min(el.values()))

  def append(self, position):
    self.positions.append(position)

  def close_position(self, position):
    if position in self.positions:
      self.positions.remove(position)


class PutButterfly(Combo):

  def __init__(self, upperlongposition, shortposition, lowerlongposition):
    self.upperlongposition = upperlongposition
    self.shortposition = shortposition
    self.lowerlongposition = lowerlongposition
    self.positions = self.upperlongposition, self.shortposition, self.lowerlongposition

#     def getPositions(self):
#         return self.positions


class PutCreditSpread(Combo):

  def __init__(self, shortposition, longposition):
    self.shortposition = shortposition
    self.longposition = longposition
    self.positions = self.shortposition, self.longposition

#     def getPositions(self):
#         return self.positions


class Strangle(Combo):

  def __init__(self, putposition, callposition):
    self.putposition = putposition
    self.callposition = callposition
    self.positions = self.putposition, self.callposition


class IronButterfly(Combo):

  def __init__(self, longcallposition, shortcallposition, shortputposition, longputposition):
    self.longcallposition = longcallposition
    self.shortcallposition = shortcallposition
    self.shortputposition = shortputposition
    self.longputposition = longputposition
    self.positions = self.longcallposition, self.shortcallposition, self.shortputposition, self.longputposition

#     def getPositions(self):
#         return self.positions


class Condor(Combo):

  def __init__(self, pcs_longposition, pcs_shortposition, pds_shortposition, pds_longposition):
    self.pcs_longposition = pcs_longposition
    self.pcs_shortposition = pcs_shortposition
    self.pds_shortposition = pds_shortposition
    self.pds_longposition = pds_longposition
    self.positions = self.pcs_longposition, self.pcs_shortposition, self.pds_shortposition, self.pds_longposition

#     def getPositions(self):
#         return self.positions


class BWB(PutButterfly):

  def __init__(self, upperlongposition, rolledlongposition, cs_shortposition, lowerlongposition):
    super(BWB, self).__init__(upperlongposition,
                              cs_shortposition, lowerlongposition)
    self.rolledlongposition = rolledlongposition

  # todo
  def getPositions(self):
    positions = []
    positions.append(self.upperlongposition)
    if self.rolledlongposition != None:
      positions.append(self.rolledlongposition)
    positions.append(self.shortposition)
    positions.append(self.lowerlongposition)
    return positions


class Group(object):

  def __init__(self, combo):
    self.combos = []
    self.combos.append(combo)

  def append(self, combo):
    self.combos.append(combo)

  def close_combo(self, combo):
    if combo in self.combos:
      self.combos.remove(combo)

  def getCombos(self):
    return self.combos


# probability that the price hits a barrier before expiry
# auch in makro
def prob_hit(s, x, t, r, sd):

  m = (r - sd ** 2 / 2)
  if (x < s):
    return 1 - st.norm.cdf((math.log(s / x) + m * t) / (sd * math.sqrt(t))) + (x / s) ** (2 * m / sd ** 2) * st.norm.cdf((math.log(x / s) + m * t) / (sd * math.sqrt(t)))
  else:
    return 1 - st.norm.cdf((math.log(x / s) - m * t) / (sd * math.sqrt(t))) + (x / s) ** (2 * m / sd ** 2) * st.norm.cdf((math.log(s / x) - m * t) / (sd * math.sqrt(t)))


def excel_date(date1):
  temp = datetime(1899, 12, 30)  # Note, not 31st Dec but 30th!
  delta = date1 - temp
  return float(delta.days) + (float(delta.seconds) / 86400)


def remaining_time(reference, expiration):

  if reference == None:
    ref = datetime.now()
  else:
    ref = datetime.combine(reference, time(15))

  ref_excel = excel_date(ref)
  ref_date = datetime(ref.year, ref.month, ref.day)
  ref_date_excel = excel_date(ref_date)
  ref_fraction = ref_date_excel - ref_excel

  expiration_excel = excel_date(expiration)
  expiration_date = datetime(expiration.year, expiration.month, expiration.day)
  expiration_date_excel = excel_date(expiration_date)
  expiration_fraction = expiration_date_excel - expiration_excel

  fraction = expiration_fraction - ref_fraction

  sessions_in_range = xnys.schedule(
      pd.Timestamp(ref.date(), tz=pytz.UTC),
      pd.Timestamp(expiration.date(), tz=pytz.UTC))

  remaining_time_in_years = (
      (len(sessions_in_range) - 1) - fraction) / yeartradingdays
  return remaining_time_in_years


def makePosition(current_date, underlying, strike, expiration, optiontype, position_size):
  try:
    option = Option(current_date, underlying, strike, expiration, optiontype)
  except ValueError:
    return None
  midprice = connector.query_midprice(current_date, option)
  position = Position(option, midprice, position_size)
  return position


def getCurrentPnLPosition(position, current_date):

  current_commissions = commissions * \
      (abs(position.amount))  # only buy, can expire
  midprice = None

  # if option is expired, compute theoretical price
  if current_date >= position.option.expiration:
    current_date = position.option.expiration
    midprice = bs_option_price(position.option.underlying, position.option.expiration,
                               position.option.type, position.option.strike, current_date)

  while midprice is None:
    midprice = connector.query_midprice(current_date, position.option)

    if midprice is None:
      current_date = current_date - timedelta(1)
      continue

  current_price = (midprice * position.amount)
  entry_price = (position.entry_price * position.amount)
  currentpnl = ((current_price - entry_price) * ratio) - current_commissions
  return currentpnl


def getCurrentPnLCombo(combo, current_date):
  currentpnl = 0
  positions = combo.getPositions()
  for position in positions:
    if position is not None:
      positionPnL = getCurrentPnLPosition(position, current_date)
      if positionPnL is None:
        return None
      currentpnl += positionPnL
  return currentpnl


def getCurrentPnLGroup(group, current_date):
  current_pnl = 0
  combos = group.getCombos()
  for combo in combos:
    if combo is None:
      print("combo is None")
    combo_pnl = getCurrentPnLCombo(combo, current_date)
    if combo_pnl is not None:
      current_pnl += combo_pnl
    else:
      print("combo_pnl is None")
  return current_pnl


def getEntryPrice(combo):
  entry_price = 0
  positions = combo.getPositions()
  for position in positions:
    if position is not None:
      entry_price += (position.entry_price * position.amount)
  return entry_price


def getDelta(combo, current_date):

  delta_sum = 0

  positions = combo.getPositions()
  for position in positions:
    if position is not None:
      delta = connector.select_delta(current_date, position.option.underlying,
                                     position.option.expiration, position.option.type, position.option.strike)
      if delta is not None:
        delta_sum += delta * position.amount

  return delta_sum


def getDeltaGroup(group, current_date):

  delta_sum = 0

  for combo in group.getCombos():
    delta_sum += getDelta(combo, current_date)

  return delta_sum


def getVega(combo, current_date):

  vega_sum = 0

  positions = combo.getPositions()
  for position in positions:
    if position is not None:
      vega = connector.select_vega(current_date, position.option.underlying,
                                   position.option.expiration, position.option.type, position.option.strike)
      if vega is not None:
        vega_sum += vega * position.amount

  return vega_sum


def getVegaGroup(group, current_date):

  vega_sum = 0

  for combo in group.getCombos():
    vega_sum += getVega(combo, current_date)

  return vega_sum


def getThetaGroup(group, current_date):

  theta_sum = 0

  for combo in group.getCombos():
    theta_sum += getTheta(combo, current_date)

  return theta_sum


def getTheta(combo, current_date):

  theta_sum = 0

  positions = combo.getPositions()
  for position in positions:
    if position is not None:
      theta = connector.select_theta(current_date, position.option)
      if theta is not None:
        theta_sum += (theta) * position.amount

  return theta_sum


def getDeltaTheta(combo, current_date):

  try:
    delta_sum = getDelta(combo, current_date)
    theta_sum = getTheta(combo, current_date)
    deltatheta = abs(delta_sum) / abs(theta_sum)
    return deltatheta
  except:
    return None


def getDeltaThetaGroup(underlying, group, current_date, expiration):

  delta_sum = 0
  theta_sum = 0

  combos = group.getCombos()
  for combo in combos:

    delta_sum += getDelta(combo, current_date)
    theta_sum += getTheta(combo, current_date)

  deltatheta_exit = abs(delta_sum) / abs(theta_sum)
  return deltatheta_exit


def getExpiration(combo, underlying_value, include_riskfree=True):

  expiration_line = 0
  positions = combo.getPositions()

  for position in positions:

    if (position is None) or (position.entry_price is None):
      return None

    rf = interest
    if include_riskfree:
      rf = get_riskfree_libor(position.option.expiration, 0)

    value = black_scholes.black_scholes(
        position.option.type, underlying_value, position.option.strike, 0, rf, 0)

    expiration = ((value - position.entry_price) * ratio *
                  position.amount - (commissions * (abs(position.amount))))
    expiration_line += expiration

  return expiration_line


def getExpirationCombo(combo):

  expirations = {}

  expirations[min_value] = getExpiration(combo, min_value)
  expirations[max_value] = getExpiration(combo, max_value)

  for position in combo.getPositions():
    expirations[float(position.option.strike)] = getExpiration(
        combo, float(position.option.strike))

  return expirations


def getExpirationGroup(group):

  lower_expiration_line = 0
  upper_expiration_line = 0

  butterflies = group.getCombos()

  for combo in butterflies:
    lower_expiration_line += getExpiration(combo, min_value)
    upper_expiration_line += getExpiration(combo, max_value)

  percentage = int(
      round((upper_expiration_line / lower_expiration_line) * ratio))
  return {'lower_expiration_line': lower_expiration_line, 'upper_expiration_line': upper_expiration_line, 'percentage': percentage}


def getQuoteforMarbleOnTop(combo, current_date, include_riskfree=True):

  lowest = combo.lowerlongposition.option.strike
  highest = combo.upperlongposition.option.strike

  quote = lowest
  max_guv = 0
  max_quote = 0

  while quote < highest:

    sum_guv = 0
    positions = combo.getPositions()
    for position in positions:

      expiration_time = datetime.combine(position.option.expiration, time(16))
      remaining_time_in_years = remaining_time(current_date, expiration_time)

      rf = interest
      if include_riskfree:
        rf = get_riskfree_libor(current_date, remaining_time_in_years)

      value = black_scholes.black_scholes(position.option.type, float(
          quote), position.option.strike, remaining_time_in_years, rf, 0)
      guv = ((value - position.entry_price) * ratio * position.amount)
      sum_guv += guv

    if (sum_guv > max_guv):
      max_guv = sum_guv
      max_quote = quote

    quote += 10

  return max_quote


def getLowerBreakpoint(combo, current_date, include_riskfree=True):

  lowest = combo.lowerlongposition.option.strike
  highest = combo.upperlongposition.option.strike

  quote = lowest
  while quote < highest:

    sum_guv = 0
    positions = combo.getPositions()
    for position in positions:

      expiration_time = datetime.combine(position.option.expiration, time(16))
      remaining_time_in_years = remaining_time(current_date, expiration_time)

      rf = interest
      if include_riskfree:
        rf = get_riskfree_libor(current_date, remaining_time_in_years)

      value = black_scholes.black_scholes(position.option.type, float(
          quote), position.option.strike, remaining_time_in_years, rf, 0)
      guv = ((value - position.entry_price) * ratio * position.amount)
      sum_guv += guv

    if (sum_guv > 0):
      return quote

    quote += 1


def getLowerBreakpointGroup(group, current_date, include_riskfree=True):

  lowest = group.getLowest().lowerlongposition.option.strike
  highest = group.getHighest().upperlongposition.option.strike

  quote = lowest
  while quote < highest:

    sum_guv = 0

    combos = group.getCombos()
    for combo in combos:
      positions = combo.getPositions()
      for position in positions:

        expiration_time = datetime.combine(
            position.option.expiration, time(16))
        remaining_time_in_years = remaining_time(current_date, expiration_time)

        rf = interest
        if include_riskfree:
          rf = get_riskfree_libor(current_date, remaining_time_in_years)

        value = black_scholes.black_scholes(position.option.type, float(
            quote), position.option.strike, remaining_time_in_years, rf, 0)
        guv = ((value - position.entry_price) * ratio * position.amount)
        sum_guv += guv

    if (sum_guv > 0):
      return quote

    quote += 1


def getDownDay(underlying, date, strategy=None):

  down_definition = 0
  if strategy == "short_term_parking":
    down_definition = -0.3

  down_day = False

  previous_date = date - timedelta(days=1)
  while ((pd.Timestamp(previous_date, tz='America/New_York') not in valid_days)
         or (connector.query_midprice_underlying(underlying, previous_date) is None) or (connector.query_midprice_underlying(underlying, previous_date) == 0)):
    previous_date = previous_date - timedelta(days=1)

  underlying_midprice_current = connector.query_midprice_underlying(
      underlying, date)
  underlying_midprice_previous = connector.query_midprice_underlying(
      underlying, previous_date)

  percentage_move = ((float(underlying_midprice_current) - float(
      underlying_midprice_previous)) / float(underlying_midprice_previous)) * 100
  if percentage_move < down_definition:
    down_day = True

  return down_day


def selectStrikeByPrice(price, underlying, date, expiration, option_type, divisor):

  results = connector.select_strikes_midprice(
      underlying, date, expiration, option_type, divisor)

  closest_strike = None

  closest_distance = 100
  closest_midprice = 0

  for row in results:
    strike = row[0]

    midprice = float(row[1])
    distance = abs(price - midprice)

    if (midprice > price) and (distance < closest_distance):
      closest_distance = distance
      closest_strike = strike
      closest_midprice = midprice

  return closest_strike, closest_midprice


def myround(x, base=25):
  return int(base * round(float(x) / base))


def testPCS(short_strike, current_date, underlying, expiration, position_size, width):

  shortposition = makePosition(
      current_date, underlying, short_strike, expiration, "p", -position_size)
  longstrike = (short_strike - width)
  longposition = makePosition(
      current_date, underlying, longstrike, expiration, "p", position_size)

  if shortposition is None or longposition is None:
    return None
  pcs = PutCreditSpread(shortposition, longposition)
  return pcs


def bs_option_price(underlying, expiration, option_type, strike, current_date, include_riskfree=True):

  price = None

  while price is None:

    current_quote = connector.query_midprice_underlying(
        underlying, current_date)
    if current_quote is None:
      current_date = current_date - timedelta(1)
      continue

    expiration_time = datetime.combine(expiration, time(16))
    remaining_time_in_years = remaining_time(current_date, expiration_time)

    rf = interest
    if include_riskfree:
      rf = get_riskfree_libor(current_date, remaining_time_in_years)

    price = black_scholes.black_scholes(
        option_type, current_quote, strike, remaining_time_in_years, rf, 0.151)

  return float(price)


def unzip(datafilepath):

  try:
    archive = zipfile.ZipFile(datafilepath)

    for ffile in archive.namelist():
      archive.extract(ffile, settings.tempbasepath)
      unzippedpath = settings.tempbasepath + ffile
      return unzippedpath

  except Exception as e:
    print(datafilepath)
    print(e)
    return None


def get_riskfree_libor(date, yte):

  # compute only once per date
  if date in functions_dict:
    f = functions_dict[date]

  else:
    try:
      df = df_yields.query('index==@date')
      dr = df.iloc[0]
      rates = ([0.0, dr['ON'] / 100, dr['1W'] / 100, dr['1M'] / 100,
               dr['2M'] / 100, dr['3M'] / 100, dr['6M'] / 100, dr['12M'] / 100])

      df_inter = pd.DataFrame(
          columns=['0', 'ON', '1W', '1M', '2M', '3M', '6M', '12M'])
      df_inter.loc[0] = years
      df_inter.loc[1] = rates
      df_inter = df_inter.dropna(axis='columns')
      f = interpol(df_inter.loc[0], df_inter.loc[1], k=1, bbox=[0.0, 4.0])
      functions_dict[date] = f

    except:
      return (0)
#             print (str(date))
#             functions_dict[date] = 0
#             f = functions_dict[date]

  y = float(yte)
  rf = f(y) / 100
  rf = np.round(rf, decimals=4)

  return rf


def make_dir(path):
  try:
    os.mkdir(path)
  except OSError:
    print('Creating dir %s failed' % path)
  else:
    print('Created dir %s ' % path)
