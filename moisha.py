#!/usr/bin/env python3
# coding: utf-8
import re
import traceback
import sys
#from ssl import OP_ENABLE_MIDDLEBOX_COMPAT
import time
import random
import telepot
import os
from os.path import isfile
from datetime import datetime  
from datetime import timedelta 
from colorama import init
from colorama import Fore
import sys
import json
import urllib.request
import sqlite3
import threading
from pycoingecko import CoinGeckoAPI
from okex import okex
cg = CoinGeckoAPI()

init() #colorama init

upd_interval = 60 #интервал обновления курсов

#Загрузка словарей=============================
reg_answers = []
def load_dic(dic):
	global reg_answers
	file = open('DICT/'+dic, 'r', encoding="utf8")
	dict = {'reg': '', 'answers': ''}
	for line in file:
		if line.startswith('^'):
			#if dict['reg'] != '': reg_answers.append(dict.copy())
			dict['reg'] = re.compile(line[:-1].lower())
			dict['answers'] = []
		else:
			if line.startswith('#'): continue
			if (line == '\n' or line == ''):
				if dict['answers'] != []:
					reg_answers.append(dict.copy())
					continue
				else:
					continue
			dict['answers'].append(line[:-1])
	file.close()
	file = open('DICT/'+dic, 'r', encoding="utf8")
	res = str(file.read().count('^'))
	print('Loaded: '+dic+' ('+res+' re)')
	file.close()
	return res

def loadreg():
	global reg_answers
	reg_answers = []
	dicts = os.listdir('DICT/')
	for file in dicts:
		#print(file)
		if file.endswith('dic'):
			load_dic(file)
		else: print(file+' не загружен')
loadreg()

#БАЗА=====================================
db = sqlite3.connect('moisha.db', check_same_thread=False)
cur = db.cursor()
db.execute('VACUUM;')
try:
	#db.execute('''CREATE TABLE IF NOT EXISTS prices(time datetime, bcinfo, polo)''')
	db.execute('''CREATE TABLE IF NOT EXISTS chat_alerts(id int, alerts)''') #[{time:'', valute:'', price:'', porog:''}]
	db.execute('''CREATE TABLE IF NOT EXISTS settings(setting UNIQUE, value)''')
except Exception as err:
	#print(err)
	print(traceback.format_exc())
db.commit()

def dict_factory(cursor, row):
	d = {}
	for idx, col in enumerate(cursor.description):
		d[col[0]] = row[idx]
	return d

db.row_factory = dict_factory

def get_data(table, cond = False):
	global db
	try:
		cur = db.cursor()
		if cond:
			cur.execute('''select * from '''+table+''' where '''+cond)
		else:
			cur.execute('''select * from '''+table)
		data = cur.fetchall()
		return data
	except Exception as err:
		#print(err)
		print(traceback.format_exc())

def get_alerts(id):
	result = get_data('chat_alerts', 'id = '+str(id))
	return json.loads(result[0]['alerts'])

def set_alert(msg, valute, porog = 1 ):
	global db
	id = msg['chat']['id']
	if not valid_valute(valute):
		say(msg, 'Не знаю такой валюты.')
		return
	valute = get_id_by_string(valute)
	cur = db.cursor()
	alerts = []
	new_list = []
	done = False
	try:
		alerts = get_alerts(id)
	except:
		cur.execute('''insert into chat_alerts (id, alerts) values (? , ?)''',(id, str([]),))
		db.commit()
	if alerts:
		for alert in alerts:
			if alert['valute'] == valute:
				alert['time'] = (datetime.now()).strftime("%d.%m.%Y %H:%M:%S")
				alert['price'] = kurs(valute)
				alert['porog'] = str(porog)
				done = True
			new_list.append(alert)
	if not done:
		alert = {'time':(datetime.now()).strftime("%d.%m.%Y %H:%M:%S"), 'valute': valute, 'price': kurs(valute), 'porog': str(porog)} #TBD криво. ты уже забрал все нужные курсы для проверки дельты, а теперь опять ходишь за каждым в процедуру получения курса для конвертера.
		new_list.append(alert)
	cur.execute('''update chat_alerts set alerts = ? where id = ?''', (json.dumps(new_list), id,))
	db.commit()
	try:
		if msg['text']:
			say(msg, 'Добавлен алерт '+valute+' с порогом '+str(porog)+'%')
	except:
		pass

def remove_alert(msg, valute):
	global db
	id = msg['chat']['id']
	if not valid_valute(valute):
		say(msg, 'Не знаю такой валюты.')
		return
	cur = db.cursor()
	alerts = []
	try:
		alerts = get_alerts(id)
	except Exception as err:
		#print(err)
		print(traceback.format_exc())
		
	if alerts:
		for alert in alerts:
			if alert['valute'] == valute:
				alerts.remove(alert)
				cur.execute('''update chat_alerts set alerts = ? where id = ?''', (json.dumps(alerts), id,))
				db.commit()
				say(msg, 'Алерт '+valute+' удалён.')
				return
		say(msg, 'Алерт '+valute+' не найден.')
	else:
		say(msg, 'Алерты для этого чата не настроены.')
		return

def get_setting(setting):
	result = get_data('settings', "setting = '"+setting+"'")
	if not result: return False
	else: 
		return result[0]['value']

def set_setting(setting, value):
	global db
	have_opt = get_setting(setting)
	try:
	#if 1:
		cur = db.cursor()
		if not have_opt:
			cur.execute('''insert into settings (setting, value) values (? , ?)''',(setting, value,))
			db.commit()
		cur.execute('''update settings set value = ? where setting = ?''', (value, setting,))
		db.commit()
		done = True
	except Exception as err:
		#print(err)
		print(traceback.format_exc())

#как называть пользователя в консоли и логах	
def user_name(msg):
	try:
		name = '@'+msg['from']['username']
	except:
		try:
			name = msg['from']['first_name']+' '+msg['from']['last_name']
		except:
			try:
				name = msg['from']['first_name']
			except:
				name = 'cid:'+str(msg['chat']['id']) #для тех случаев когда мы задаём пустое сообщение со своим id
	return name

#собсна бот
class YourBot(telepot.Bot):
	def __init__(self, *args, **kwargs):
		super(YourBot, self).__init__(*args, **kwargs)
		self._answerer = telepot.helper.Answerer(self)
		self._message_with_inline_keyboard = None
		
	def on_chat_message(self, msg):
		content_type, chat_type, chat_id = telepot.glance(msg)
		
		with open('tg.log', 'a', encoding="utf8") as log:
			log.write(str(msg) + '\n')
			
		if (content_type == 'new_chat_member'): 
			bot.sendSticker(msg['chat']['id'], random.choice(['CAADAgADnAEAAr8cUgGqoY57iHWJagI','CAADAgADWAEAAr8cUgHoHDucQspSKwI']))	
		
		if content_type != 'text':
			print(datetime.now().strftime("%d.%m.%Y %H:%M:%S")+' '+content_type, chat_type, chat_id)
			return
		try:
			print(Fore.RED +datetime.now().strftime("%d.%m.%Y %H:%M:%S")+' '+user_name(msg)+":", msg['text']+Fore.WHITE)
		except UnicodeEncodeError: 
			print('UnicodeEncodeError')
		
		process(msg)
		
	def on_edited_chat_message(self, msg):
		pass

def say(msg,answer,silent = False):
	#обработка ключевых слов из словаря
	if '[name]' in  answer: answer = answer.replace('[name]', user_name(msg))
	if '[br]' in  answer:
		answer = answer.replace('[br]', '\n')
	if '[courses]' in  answer:
		try:
			alerts = get_alerts(msg['chat']['id'])
		except:
			alerts = False
		if alerts:
			stringg = ''
			for alert in alerts:
				stringg+= '*'+get_sym_by_id(alert['valute'])+' ('+alert['valute']+')*: '+str(kurs(alert['valute']))+'$\n'
		else:
			stringg = 'Настрой алерты /alert valute'	
		answer = answer.replace('[courses]', stringg)
	if len(answer)>4096:
		parts = []
		text = answer
		while len(text) > 0:
			if len(text) > 4096:
				part = text[:4096]
				first_lnbr = part.rfind('\n')
				if first_lnbr != -1:
					parts.append(part[:first_lnbr])
					text = text[first_lnbr:]
				else:
					parts.append(part)
					text = text[4096:]
			else:
				parts.append(text)
				break

		for part in parts:
			bot.sendMessage(msg['chat']['id'], part, parse_mode='Markdown', disable_web_page_preview = True)
	else:
		bot.sendMessage(msg['chat']['id'], answer, parse_mode='Markdown', disable_web_page_preview = True)
	if not silent:
		try:
			print(Fore.GREEN +datetime.now().strftime("%d.%m.%Y %H:%M:%S")+' Мойша (to '+user_name(msg)+'): '+answer+Fore.WHITE)
		except UnicodeEncodeError: 
			print('UnicodeEncodeError')

def getcourses():
	global cg,valutes
	try:
	#if 1:
		data = cg.get_price(ids= valutes, vs_currencies='usd')
		#print(data)
		do_chat_alerts(data)
		recheck_list()
	except Exception as err:
		print(err)
		#print(traceback.format_exc())
	getcourses_timer = threading.Timer(upd_interval, getcourses)
	getcourses_timer.name = 'getcourses_timer'
	getcourses_timer.start()

def valid_valute(valute):
	ids = get_id_by_string(valute)
	if ids: 
		return True
	else:
		return False

def kurs (valute):
	global cg
	if not valid_valute(valute):
		return False 
	id = get_id_by_string(valute)
	result = cg.get_price(ids=id, vs_currencies='usd') 
	return float(result[id]['usd'])

def tonmine (power_consumption = 0.1, power_price = 5):
	# ton mining profitablity
	#power_consumption = 0.1 # kwt\h per Gh
	#power_price = 5 #roubles per kwt\h
	request = urllib.request.Request('https://ton-reports-24d2v.ondigitalocean.app/report/pool-profitability')
	response = urllib.request.urlopen(request)
	json_received = (response.read()).decode('utf-8', errors='ignore')
	p = json.loads(json_received)
	profitablility = int(p['profitabilityPerGh'])/(10**9)
	#print(profitablility)
	net_cost = (power_consumption*24*power_price)/profitablility
	return 'avg prof: '+ str(profitablility)+' Gh\day\n'+str(round(net_cost,2))


def get_info_from_id(id):
	try:
		q = cg.get_coin_by_id(id)
	except ValueError:
		return 'id '+id+' not found.'

	#except Exception as err:
	#	return str(err)
	#print(json.dumps(q))
	try:
		answ = '*ID: *'+q['id']+'\n'+'*SYM: *'+q['symbol']+'\n'+'*Name: *'+q['name']+'\n'
	except TypeError:
		return 'id '+id+'type error.'

	try:
		answ += '*Цена: * ~'+str(q['market_data']['current_price']['rub'])+' руб.\n'
	except:
		try:
			answ += '*Price: * ~'+str(q['market_data']['current_price']['usd'])+' $.\n'
		except:
			answ += '*Нет данных по цене.*\n'
	if q['categories']:
		answ+='*categories: *'
		for cat in q['categories']:
			if cat: answ+=cat+', ' #бывают None в списке категорий хз с чем свяяано
		answ+='\n'
	#print(answ)
	for key, value in q['links'].items():
		if not value: continue
		#print (key+str(value))
		links = ''
		if type(value) is list:
			for i in value:
				if i:
					links+=i+'\n'
		elif type(value) is str:
			links += value+'\n'
		elif type(value) is dict:
			for i in value.values():
				if i: links+=', '.join(i)+'\n'
		#print(links)
		if links:
			#print (key)
			#print (links)
			if key == 'telegram_channel_identifier': 
				key = 'telegram'
				links = '@'+links
			links = links.replace('_', '\_')
			answ+= '*'+key+':* \n'
			answ+= links
	#'''#TBD выдели в отдельную команду, вытри рефки.
	try:
		if q['tickers']:
			answ+='*markets:*\n'
			for i in q['tickers']:
				try:
					url = i['trade_url']
				except:
					pass
				if url: 
					answ+=(i['target']+' on ['+i['market']['name']+']('+url+')\n') ##[hello](https://t.me/)
				else:
					answ+=(i['target']+' on '+i['market']['name']+'\n') ##[hello](https://t.me/)
	except KeyError:
		pass
	#'''

	return answ

def process (msg):
	global reg_answers, pause, db, run, coins_list
	answer = ''
	msg['text'] = (msg['text']).replace('@'+bot.getMe()['username'],'')
	if (msg['text'].lower().startswith('/alert')):
		try:
			if (msg['text'].lower() == '/alert'):
				say (msg, "'/alert valute' or '/alert valute porog'")
			elif (msg['text'].lower() == '/alerts'):
				alerts = get_alerts(msg['chat']['id'])
				stringg = ''
				for alert in alerts:
					stringg += '*'+get_sym_by_id(alert['valute'])+' ('+alert['valute']+')* - '+alert['porog']+'%\n'
				stringg = stringg.rstrip()
				if stringg:
					say(msg,stringg)
				else:
					say(msg,'Алерты не настроены.')
			else:
				curr = (msg['text'].lower()).partition('/alert ')[2]
				porog = ''
				if ' 'in curr:
					porog = curr.partition(' ')[2]
					curr = curr.partition(' ')[0]
				if porog:
					if porog.isdigit():
						set_alert(msg, curr, int(porog))
					else:
						say(msg, 'Неверное значние порога, введите число.')
				else:
					set_alert(msg, curr)
		except Exception as err:
			#print(err)
			print(traceback.format_exc())
			pass
		return
	if (msg['text'].lower().startswith('/noalert')):
		try:
			if (msg['text'].lower() == '/noalert'):
				say(msg, 'Алерт на какую валюту удалить?')
			elif (msg['text'].lower() == '/noalerts'):
				id = msg['chat']['id']
				cur = db.cursor()
				cur.execute('''update chat_alerts set alerts = ? where id = ?''', (json.dumps([]), id,))
				db.commit()
				say(msg,'Все алерты удалены.')
			else:
				curr = (msg['text'].lower()).partition('/noalert ')[2]
				remove_alert(msg, curr)
		except Exception as err:
			#print(err)
			print(traceback.format_exc())
			pass
		return
	if (msg['text'].lower().startswith('/newalerts')): #уведомлять о новых монетах
		try:
			na = get_setting('newalerts')
			if not na:
				newalerts = [msg['chat']['id']]
				set_setting('newalerts', json.dumps(newalerts))
			else:
				newalerts = json.loads(get_setting('newalerts'))
				id = msg['chat']['id']
				if id not in newalerts:
					newalerts.append(id)
					set_setting('newalerts', json.dumps(newalerts))
					say(msg,'Буду уведомлять о новых койнах.')
				else:
					say(msg,'Уже уведомляю тебя о новых койнах.')
		except Exception as err:
			#print(err)
			print(traceback.format_exc())
			pass
		return
	if (msg['text'].lower().startswith('/nonewalerts')): #не уведомлять о новых монетах
		try:
			na = get_setting('newalerts')
			if na:
				newalerts = json.loads(get_setting('newalerts'))
				id = msg['chat']['id']
				if id in newalerts:
					newalerts.remove(msg['chat']['id'])
					set_setting('newalerts', json.dumps(newalerts))
					say(msg,'Не буду уведомлять о новых койнах.')
				else:
					say(msg,'Я не уведомляю тебя о новых койнах.')
		except Exception as err:
			#print(err)
			print(traceback.format_exc())
			pass
		return
	if (msg['text'].lower().startswith('/search')): #
		if (msg['text'].lower() == '/search'):
			say (msg, "What to search?")
		elif (msg['text'].lower().startswith('/search ')):
			string = (msg['text'].lower()).partition('/search ')[2]
			try:
				global coins_list
				found = []
				answ = '*search results:*\n'
				for i in coins_list:
					for j in i.values():
						if string in j: 
							if i not in found: found.append(i)
				for i in found:
					answ += str(i)+'\n'
				#print(answ)
				say(msg,answ)
			except Exception as err:
				#print(err)
				print(traceback.format_exc())
				pass
		return
	if (msg['text'].lower().startswith('/info')): #
		if (msg['text'].lower() == '/info'):
			say (msg, "which coin id info you want?")
		elif (msg['text'].lower().startswith('/info ')):
			coin_id = (msg['text'].lower()).partition('/info ')[2]
			try:
			#if 1:
				for i in coins_list:
					if i['id'] == coin_id:
						answ = get_info_from_id(i['id'])
						'''testing filter
						fb = filter_bullshit(answ)
						if len(fb)>1:
							answ = answ.split('\n')[0]+'\n'
							answ+= '*is bullshit because of:*\n'
							for reason in fb:
								answ+=reason+', '
						'''
						say(msg,answ+'\n')
						return
				else: say(msg,'id '+coin_id+' не найден. Попробуй /search ' + coin_id + ' и выбери подходящий.')
			except Exception as err:
				#print(err)
				print(traceback.format_exc())
				say(msg,'id '+coin_id+', ошибка обработки данных.')
			#	pass
		return
	if (msg['text'].lower().startswith('/mine')): 
		try:
		#if 1:
			net_cost = tonmine (power_consumption = 0.1, power_price = 5)	
			say(msg,net_cost+'р. за тон при 100w\gh и 5р\kwh')
		except Exception as err:
			#print(err)
			print(traceback.format_exc())
		#	pass
		return

	if (msg['text'].lower().startswith('/reload')):
		try:
			try:
				usercheck = msg['from']['username']
			except:
				usercheck = "nobody"
			if (usercheck != "Brakhma"):
				say(msg, "Permission denied!")
				intruder = user_name(msg)+' TRIES TO RELOOOAD!'
				print (intruder)
				return
			os.system("git pull")
			#os.system("python3 "+__file__)
			stopthreads()
			run = False
		except Exception as err:
			#print(err)
			print(traceback.format_exc())
			pass
		return
	if (msg['text'].lower() == ('/fund')): #
		f = get_setting('fund_shares')
		if not f: return
		shares = json.loads(get_setting('fund_shares'))
		#print(shares)
		shares_total = 0
		my_cut = 0
		for i in shares:
			shares_total+=int(i[1])
			if int(i[0]) == msg['from']['id']:
				my_cut = int(i[1])
		if my_cut == 0:
			say(msg, 'Таки тьфу на тебя.')
			return
		perc = round((my_cut/shares_total)*100, 2) 
		#print(str(my_cut)+' '+str(perc)+'%')
		fund_str='*Структура фонда:*\n'
		ok = okex(api_key=okex_apikey, api_secret=okex_secret, passphrase=okex_passphrase)
		balances = ok.get_balances()
		total_eq = 0
		for i in balances:
			total_eq+=float(i['eqUsd'])
			q = (i['ccy']+': '+i['cashBal'])
			fund_str+=q+'\n'
		orders = (ok.get_orders())
		if orders: fund_str+='*Открытые ордера:*\n'
		for i in orders:
			fund_str+=i['instId']+' '+i['side']+' '+i['sz']+' x '+i['px']+'\n'
		fund_str+='*Цена активов:*\n'
		req = cg.get_price(ids='tether', vs_currencies='rub')
		result = total_eq*float(req['tether']['rub'])
		fund_str+= '~'+str(round(result,2))+'₽\n'
		fund_str+= '*Твоя доля:*\n'
		my_cut_rub = round(result*(my_cut/shares_total),2)
		fund_str+= str(my_cut_rub)+'₽ ('+str(perc)+'%)\n'
		fund_str+= '\*без учёта комиссий за конвертацию и вывод.'
		say(msg, fund_str)
		return
	if (msg['text'].lower().startswith('/add_shares ')):
		try:
			try:
				usercheck = msg['from']['username']
			except:
				usercheck = "nobody"
			if (usercheck != "Brakhma"):
				say(msg, "Permission denied!")
				#intruder = user_name(msg)+' TRIES TO ADD SHARES!'
				#print (intruder)
				return
			f = get_setting('fund_shares')
			#TBD при добавлении новой доли берём текущую цену активов и делим по долям, чтобы не делить на новичка прибыль\убыток от старых сделок.
			if not f: f = '[]'
			q = (msg['text'].lower()).partition('/add_shares ')[2]
			new_shares = q.split(', ')
			shares = json.loads(f)
			shares.append(new_shares)
			set_setting('fund_shares', json.dumps(shares))
			#print(shares)
			say(msg, 'Added '+str(new_shares)+'\n'+str(shares))
			return
		except Exception as err:
			say(msg, str(err))
			#print(err)
			print(traceback.format_exc())
	#конвертер
	#TBD есть крипта с цифрами в тикере, типа 1sol по-хорошему надо искать части строки в списке валют, есть ещё например такое $ryu
	#да и хуй с ней. %)
	if re.match('((\d+\.\d+)|(\d+))( )([a-zA-Z]+)( to )([a-zA-Z]+)', msg['text'].lower()):
		answ = converter(msg['text'].lower())
		say(msg, answ)
		return
	
	if re.match('((\d+\,\d+)|(\d+))( )([a-zA-Z]+)( to )([a-zA-Z]+)', msg['text'].lower()):
		repl = (msg['text'].lower()).replace(',','.')
		answ = converter(repl)
		say(msg, answ)
		return

	#обработка регулярок из словаря 
	#/kurs в словаре, туда лучше складывать всё что требует нескольких алиасов или регулярки для запуска
	for pair in reg_answers: 
		if (re.match(pair['reg'], msg['text'].lower())):
			answers = pair['answers']
			answer = random.choice(answers)
			say(msg, answer)
			return

#иниициализация
from settings import *

'''
if not isfile('tgtoken'):
	TOKEN = input('Введи Bot-API токен:')
	open('tgtoken', 'w', encoding="utf8").write(TOKEN)
else:
	TOKEN = (open('tgtoken', encoding="utf8").read()).rstrip()
if not isfile('proxy'):
	proxxx = input('Введи https_proxy:port или жмякни enter если не нужен:')
	open('proxy', 'w', encoding="utf8").write(proxxx)
else:
	proxxx = (open('proxy', encoding="utf8").read()).rstrip()
if proxxx: telepot.api.set_proxy('https://'+proxxx)
'''
bot = YourBot(TOKEN)
bot.message_loop()

print (Fore.YELLOW + bot.getMe()['first_name']+' (@'+bot.getMe()['username']+')'+Fore.WHITE)


def stopthreads():
	for thing in threading.enumerate():
		if isinstance(thing, threading.Timer):
			thing.cancel()
	print ("Все потоки успешно завершены.")

def printthreads():
	strr=''
	for thing in threading.enumerate():
		strr+= str(thing)+'\n'
	print(strr)
	return(strr)

def do_chat_alerts(prices):
	global valutes
	try:
		now_crs = prices #{'ethereum': {'usd': 4003.22}, 'litecoin': {'usd': 153.91}, 'bitcoin': {'usd': 48812}, 'the-open-network': {'usd': 2.52}}
		chat_alerts = get_data('chat_alerts')
		#print (chat_alerts)
		for chat in chat_alerts:
			#print (chat)
			alerts = json.loads(chat['alerts'])
			#обновляем список валют для следующего забора курсов
			for alert in alerts:
				if alert['valute'] not in valutes: valutes+=alert['valute']+','
			
			msg = {'chat': {'id': chat['id']}}
			for key, value in now_crs.items():
				dta = False
				for alert in alerts:
					if alert['valute'].lower() == key:
						dta = alert
						break
				if dta:
					old_prc = dta['price']
					old_tim = dta['time']
					porog = int(dta['porog'])
					value = value['usd']
					chg = ((value - old_prc)/value)*100
					timediff = (datetime.now() - datetime.strptime(old_tim, "%d.%m.%Y %H:%M:%S"))
					
					if abs(chg)>porog:
						if chg > 0:
							chg_str = '▲'#random.choice(['вырос', 'пульнул', 'отрос', 'поднялся'])
						else:
							chg_str = '▼'#random.choice(['упал', 'дропнулся', 'рухнул', 'опустился'])
						if timediff.days: strtd = str(timediff.days) + ' д.'
						elif (timediff.seconds//3600)>0: strtd = str(timediff.seconds//3600)+' ч.'
						else:
							mins = (timediff.seconds//60)%60
							strtd = str(mins) +' мин.'
						val_str = get_sym_by_id(key)+' ('+key+')' #key
						res_str = val_str+'  '+str(int(abs(chg)))+'%'+chg_str+'  за '+str(strtd)+'   '+str(value) #zec  2%▲  за 1 ч.   0.05111
						#res_str = val_str+' '+str(value)+' ('+str(int(abs(chg)))+'%'+chg_str+') за '+str(strtd) #zec 0.05111 (2%▲) за 1 ч.
						set_alert(msg, dta['valute'], int(dta['porog']))
						say(msg,res_str)
	except Exception as err:
		#print(err)
		print(traceback.format_exc())
		pass

def get_id_by_string(string):
	global coins_list
	found = []
	for i in coins_list:
		if (i['id'] == string.lower()) or (i['symbol'] == string.lower()):
			if i not in found: found.append(i)
	if len(found) == 1: 
		return found[0]['id']
	elif len(found) == 0:
		return False
	else: 
		leader = ''
		for i in found:
			q = cg.get_coin_by_id(i['id'])
			#print(q['id']+' '+str(q['coingecko_score']))
			if not leader: 
				leader = q
			else:
				if q['coingecko_score'] > leader['coingecko_score']:
					leader = q
		#print(leader['id']+' '+str(leader['coingecko_score']))
		return leader['id']

def get_sym_by_id(string):
	global coins_list
	for i in coins_list:
		if (i['id'] == string.lower()):
			return i['symbol']

def converter(constr):
	#TBD гекко поддерживает конвертацию только для тех пар для которых она рельно есть, а их мало. надо реализовать старую двухступенчатую конвертацию через биток
	global cg
	#print (constr)
	constr = constr.replace(',', '.')
	if re.match('((\d+\.\d+)|(\d+))( )([a-zA-Z]+)( to )([a-zA-Z]+)', constr):
		towork = constr.split(' ')
	else:
		return ('Invalid format. ("666 btc to usd")')	
	#[num,cur1,to,cur2]
	#print(towork)
	num = float(towork[0])
	cur1 = towork[1]
	cur2 = towork[3]

	if valid_valute(cur1):
		cur1 = get_id_by_string(cur1)
		vses = cg.get_supported_vs_currencies()
		if cur2 in vses:
			req = cg.get_price(ids=cur1, vs_currencies=cur2)
			result = num*float(req[cur1][cur2])
			return(towork[0]+ ' '+get_sym_by_id(cur1)+' ('+cur1+') = '+str(result)+' '+cur2)
		else:
			return ("Don't know about "+cur2+" valid vs is "+ str(vses))
	else:
		#пытаемся перевернуть строку и посчитать обратный курс:
		if valid_valute(cur2):
			cur2 = get_id_by_string(cur2)
			vses = cg.get_supported_vs_currencies()
			if cur1 in vses:
				req = cg.get_price(ids=cur2, vs_currencies=cur1)
				result = num/float(req[cur2][cur1])
				return(towork[0]+ ' '+cur1+' = '+str(round(result,8))+ ' '+get_sym_by_id(cur2)+' ('+cur2+')')
			else:
				return ("Don't know about "+cur1)
		else:
			return ("Don't know about "+cur2)

valutes = '' #обновляется на каждой итерации do_chat_alerts(prices) лень переделывать add remove etc

#получаем лист койнов, сверяем с листом из базы
coins_list = cg.get_coins_list()

def filter_bullshit(coin_info):
	strikes = []
	coin_info = coin_info.lower()
	stop_words = ['meta','shiba','inu','meme','zilla','doge', 'verse', 'floki', 'baby']
	if (' not found.' in coin_info): return []
	
	#filtering L2
	if "binance smart chain ecosystem" in coin_info: strikes.append('L2 BSC')
	elif ('https://ethplorer.io' in coin_info) or ('https://etherscan.io' in coin_info): strikes.append('L2 ETH')		
	elif ('https://solscan.io' in coin_info) or ('https://explorer.solana.com/' in coin_info): strikes.append('L2 SOL')
	elif ('polygon ecosystem' in coin_info): strikes.append('L2 Polygon')
	elif ('cardanoscan.io' in coin_info): strikes.append('L2 Cardano')
	elif ('https://cronoscan.com' in coin_info) or ('https://cronos.crypto.org' in coin_info) or ('https://cronos-explorer.crypto.org' in coin_info): strikes.append('L2 Cronos')
	elif ('https://ftmscan.com' in coin_info): strikes.append('L2 Fantom')
	elif ('https://snowtrace.io' in coin_info): strikes.append('L2 Avalanche')
	elif ('arbiscan.io' in coin_info): strikes.append('L2 Arbitrum')
	elif ('https://tronscan.org' in coin_info): strikes.append('L2 Tron')
	elif ('metis.io' in coin_info): strikes.append('L3 Metis')

	if "non-fungible tokens (nft)" in coin_info:
		strikes.append('NFT') 

	#filtering stopwords
	for word in stop_words:
		if word in coin_info:
			strikes.append('word '+ word)

	#filtering closed source
	if not 'repos_url' in coin_info:
		strikes.append('closed source')
	else:
		ci = coin_info.split('\n')
		for line in ci:
			if 'github' in line:
				try:
					response = urllib.request.urlopen(line)
					xx = (response.read().decode('utf-8'))
					#print(xx)
					if ("doesn&#39;t have any public repositories yet." in xx) or ("This organization has no public repositories" in xx):
						strikes.append('empty repo')
					else:
					#TBD check empty repo ('doesn&#39;t have any public repositories yet.') 'This organization has no public repositories'
					#TBD forked repos (/repositories 'Forked from')
						#strikes = [] #all heil to opensource
						pass
				except urllib.error.HTTPError as err:
					print(err) 
	return strikes

def recheck_list():
	global coins_list,cg
	try:
		coins_list = cg.get_coins_list()
	except Exception as err:
		#print(err)
		print(traceback.format_exc())
	ocl = get_setting('coins_list')
	if ocl:
		old_coins_list = json.loads(ocl)
		diff = [i for i in coins_list if i not in old_coins_list]
		if diff:
			#print(Fore.RED+'NEW DIFF!'+Fore.WHITE)
			#print (str(diff))
			newalerts = json.loads(get_setting('newalerts'))
			suff=''
			if len(diff)>1:suff+='s'
			answer = ''
			for id in newalerts:
				msg = {'chat': {'id': id}}
				answer+=str(id)+', '
				#print(id)
				diffstr = ''
				for i in diff:
					nfo = get_info_from_id(i['id'])
					#diffstr+=str(i)+'\n'
					#filtering bullshit
					fb = filter_bullshit(nfo)
					if len(fb)<2:
						diffstr+= nfo+' ====================\n'
					else:
						diffstr+= nfo.split('\n')[0]+'\n'
						diffstr+= '*is bullshit because of:*\n'
						for reason in fb:
							diffstr+=reason+', '
						diffstr+='\n ====================\n'

				say(msg, '*new coin'+suff+' listed:*\n\n'+diffstr,silent = True)
			set_setting('coins_list',json.dumps(coins_list))
			print(Fore.GREEN +datetime.now().strftime("%d.%m.%Y %H:%M:%S")+' Newcoin alerts to: '+answer+Fore.WHITE)
		#else: print ('no diff')
	else:
		set_setting('coins_list',json.dumps(coins_list))

getcourses()

run = True
try:
	while run:
		time.sleep(2)
	os.system("./reload.sh &") #запустится только при /reload	
except KeyboardInterrupt:
	stopthreads()
	db.commit()
	db.close()