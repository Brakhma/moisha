#!/usr/bin/env python3
# coding: utf-8
import re
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

init() #colorama init

logfolder = "/var/www/brakhma.ru/html/botlogs/"
upd_interval = 300 #интервал обновления курсов

#~~~~~~~~~MODULES
try:
	os.chdir(os.path.dirname(__file__))
except:
	pass
from cryptoconverter import *

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
	db.execute('''CREATE TABLE IF NOT EXISTS prices(time datetime, bcinfo, polo)''')
	db.execute('''CREATE TABLE IF NOT EXISTS chat_alerts(id int, alerts)''') #[{time:'', valute:'', price:'', porog:''}]
	db.execute('''CREATE TABLE IF NOT EXISTS settings(setting, value)''')
except Exception as err:
	weblog('db create tables\n'+str(err))
	print(err)
db.commit()

def dict_factory(cursor, row):
	d = {}
	for idx, col in enumerate(cursor.description):
		d[col[0]] = row[idx]
	return d

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
		weblog('get_data\n'+str(err))
		print(err)

def set_prices(bcinfo, polo):
	global db
	done = False
	while not done:
		try:
		#if 1:
			cur = db.cursor()
			cur.execute('''insert into prices (time, bcinfo, polo) values (? , ? , ?)''', (datetime.now(), bcinfo, polo))
			db.commit()
			done = True
		except Exception as err:
			weblog('set_prices\n'+str(err))
			print(err)


prices_cache = None
def get_prices(time):
	global db, prices_cache
	if prices_cache: # слишком часто ходим в базу, бессмысленно и дорого.
		timediff = (datetime.now() - datetime.strptime(prices_cache['time'][0:19], "%Y-%m-%d %H:%M:%S"))
		#print(timediff)
		if timediff.seconds < upd_interval: 
			#print(mins)
			return prices_cache
	db.row_factory = dict_factory
	cur = db.cursor()
	try:
		cur.execute('''select * from prices where time < ? order by time desc''', (time,))
	except Exception as err:
		print(err)
		weblog('get_prices\n'+str(err))
	data = cur.fetchone()
	#print(data)
	prices_cache = data
	return data

def get_alerts(id):
	result = get_data('chat_alerts', 'id = '+str(id))
	return json.loads(result[0]['alerts'])

def set_alert(msg, valute, porog = 1 ):
	global db
	id = msg['chat']['id']
	if valute == 'btc':
		valute = 'usd'
	if not valid_valute(valute):
		say(msg, 'Не знаю такой валюты.')
		return
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
		alert = {'time':(datetime.now()).strftime("%d.%m.%Y %H:%M:%S"), 'valute': valute, 'price': kurs(valute), 'porog': str(porog)}
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
	if valute == 'btc':
		valute = 'usd'
	if not valid_valute(valute):
		say(msg, 'Не знаю такой валюты.')
		return
	cur = db.cursor()
	alerts = []
	try:
		alerts = get_alerts(id)
	except Exception as err:
		weblog('remove_alert\n'+str(err))
		print(err)
		
	if alerts:
		for alert in alerts:
			if alert['valute'] == valute:
				alerts.remove(alert)
				break
	else:
		say(msg, 'Алерты для этого чата не настроены.')
		return
	cur.execute('''update chat_alerts set alerts = ? where id = ?''', (json.dumps(alerts), id,))
	db.commit()
	say(msg, 'Алерт '+valute+' удалён.')

def get_setting(setting):
	result = get_data(settings, 'setting = '+setting)
	return result['value']

def set_setting(setting, value):
	global db
	done = False
	try:
		have_opt = get_setting(setting)
	except:
		have_opt = False
	while not done:
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
			weblog('set_setting\n'+str(err))
			print(err)

#как называть пользователя в консоли и логах	
def user_name(msg):
	try:
		name = '@'+msg['from']['username']
	except:
		try:
			name = msg['from']['first_name']+' '+msg['from']['last_name']
		except:
			name = msg['from']['first_name']
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

def weblog(param):
	try:
		with open(logfolder+'moisha.txt', 'a', encoding="utf8") as log:
			log.write((datetime.now()).strftime("%d.%m.%Y %H:%M:%S") + '\n'+param+'\n')
	except FileNotFoundError:
		print('Weblog folder not found.')
		pass
	except Exception as err:
		print(err)
		pass

def say(msg,answer):
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
				stringg+= '*'+alert['valute']+'*: '+str(kurs(alert['valute']))+'\n'
			stringg+=kurs()
		else:
			stringg = 'Настрой алерты /alert valute'	
		answer = answer.replace('[courses]', stringg)
		
	bot.sendMessage(msg['chat']['id'], answer, parse_mode='Markdown', disable_web_page_preview = True)
	try:
		print(Fore.GREEN +datetime.now().strftime("%d.%m.%Y %H:%M:%S")+' Мойша: '+answer+Fore.WHITE)
	except UnicodeEncodeError: 
		print('UnicodeEncodeError')

def getcourses():
	#blockchain.info
	success = True
	try:
	
		request = urllib.request.Request('https://blockchain.info/ticker')
		response = urllib.request.urlopen(request)
		received_data = (response.read()).decode('utf-8')
		bcinfo = received_data
	except Exception as err:
		print(err)
		weblog('getcourses bcinfo\n'+str(err))
		success = False
	#poloniex
	try:
		request = urllib.request.Request('https://poloniex.com/public?command=returnTicker')
		response = urllib.request.urlopen(request, timeout = 20)
		received_data = (response.read()).decode('utf-8')
		polo = received_data
	except Exception as err:
		print(err)
		weblog('getcourses polo\n'+str(err))
		success = False
	getcourses_timer = threading.Timer(upd_interval, getcourses)
	getcourses_timer.name = 'getcourses_timer'
	getcourses_timer.start()
	if success:
		set_prices(bcinfo,polo)
		do_chat_alerts()
	else:
		return False

def is_crypto(curr):
	if curr.upper() == 'USDT': return False
	if curr.upper() == 'BTC': return True
	raw_data = get_prices(datetime.now())['polo']
	results = json.loads(raw_data)
	alc = []
	for key in results.keys():
		if not key.startswith('BTC_'):continue
		crpt = (key.partition('BTC_')[2])
		alc.append(crpt)
	if curr.upper() in alc:
		return True
	else:
		return False

def is_fiat(curr):
	raw_data = get_prices(datetime.now())['bcinfo']
	results = json.loads(raw_data)
	alc = []
	for key in results.keys():
		alc.append(key)
	if curr.upper() in alc:
		return True
	else:
		return False

def course_fiat(valute):
	if not is_fiat(valute): return False
	raw_data = get_prices(datetime.now())['bcinfo']
	results = json.loads(raw_data)
	return float(results[valute.upper()]['last'])

def course_crypto(valute):
	if not is_crypto(valute): return False
	raw_data = get_prices(datetime.now())['polo']
	results = json.loads(raw_data)
	return float(results['BTC_'+valute.upper()]['last'])

def valid_valute(valute):
	valute = valute.upper()
	if is_crypto(valute): return True
	elif is_fiat(valute): return True
	else: return False

def kurs (valute = False):
	if not valute: #отдаём время получения курса
		return get_prices(datetime.now())['time']
	if not valid_valute(valute):
		return False 
	if is_crypto(valute):
		return course_crypto(valute)
	if is_fiat(valute):
		return course_fiat(valute)

def process (msg):
	global reg_answers, pause, db, run
	answer = ''
	if (msg['text'].lower().startswith('/alert')):
		try:
			if (msg['text'].lower() == '/alert'):
				say (msg, "'/alert valute' or '/alert valute porog'")
			elif (msg['text'].lower() == '/alerts'):
				alerts = get_alerts(msg['chat']['id'])
				stringg = ''
				for alert in alerts:
					stringg += '*'+alert['valute']+'* - '+alert['porog']+'%\n'
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
			print (err)
			weblog('process alert\n'+str(err))
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
			print (err)
			weblog('process noalert\n'+str(err))
			pass
		return
	if (msg['text'].lower().startswith('/reload')):
		try:
			try:
				usercheck = msg['from']['username']
			except:
				usercheck = "nobody"
			if (usercheck != "Brakhma"):
				say("Permission denied!")
				intruder = user_name(msg)+' TRIES TO RELOOOAD!'
				print (intruder)
				weblog(intruder)
				return
			os.system("git pull")
			#os.system("python3 "+__file__)
			stopthreads()
			run = False
		except Exception as err:
			print (err)
			weblog('reload error\n'+str(err))
			pass
		return

	#конвертер
	if re.match('((\d+\.\d+)|(\d+))( )([a-zA-Z]+)( to )([a-zA-Z]+)', msg['text'].lower()):
		answ = convertor(msg['text'].lower())
		say(msg, answ)
		return
	
	if re.match('((\d+\,\d+)|(\d+))( )([a-zA-Z]+)( to )([a-zA-Z]+)', msg['text'].lower()):
		repl = (msg['text'].lower()).replace(',','.')
		answ = convertor(repl)
		say(msg, answ)
		return

	#обработка регулярок из словаря 
	#/kurs в словаре, туда лучше складывать всё что требует нескольких алиасов или регулярки для запуска
	for pair in reg_answers: 
		if (re.match(pair['reg'], msg['text'].lower())):
			answers = pair['answers']
			answer = random.choice(answers)
			say(msg,answer)
			return

if not isfile('tgtoken'):
	TOKEN = input('Введи Bot-API токен:')
	open('tgtoken', 'w', encoding="utf8").write(TOKEN)
else:
	TOKEN = (open('tgtoken', encoding="utf8").read()).rstrip()
'''
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
weblog('started')

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

def make_pricelist(prices):
	pricelist = {}
	raw_data = prices['bcinfo']
	results = json.loads(raw_data)
	for key, value in results.items():
		pricelist[key] = value['last']
	raw_data = get_prices(datetime.now())['polo']
	results = json.loads(raw_data)
	for key, value in results.items():
		if not key.startswith('BTC'): continue
		pricelist[key[4:]] = float(value['last'])
	#print(pricelist)
	return pricelist

def do_chat_alerts():
	try:
		now_crs = make_pricelist(get_prices(datetime.now()))
		#old_crs = make_pricelist(get_prices(datetime.now() - timedelta(hours = 1)))
		#if not old_crs: continue
		chat_alerts = get_data('chat_alerts')
		for chat in chat_alerts:
			alerts = json.loads(chat['alerts'])
			msg = {'chat': {'id': chat['id']}}
			for key, value in now_crs.items():
				dta = False
				for alert in alerts:
					if alert['valute'].upper() == key:
						dta = alert
						break
				if dta:
					old_prc = dta['price']
					old_tim = dta['time']
					porog = int(dta['porog'])
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
						val_str = key
						if val_str == 'USD': val_str = 'Биток'
						elif val_str == 'ETH': val_str = 'Эфир'
						elif val_str == 'RUB': val_str = 'Биток к рублю'
						res_str = val_str+'  '+str(int(abs(chg)))+'%'+chg_str+'  за '+str(strtd)+'   '+str(value) #zec  2%▲  за 1 ч.   0.05111
						#res_str = val_str+' '+str(value)+' ('+str(int(abs(chg)))+'%'+chg_str+') за '+str(strtd) #zec 0.05111 (2%▲) за 1 ч.
						set_alert(msg, dta['valute'], int(dta['porog']))
						say(msg,res_str)
	except Exception as err:
		weblog('do alerts\n'+str(err))
		print(err)

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