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
from datetime import datetime, timedelta # ИЗМЕНЕНО: timedelta импортирован явно
from colorama import init, Fore # ИЗМЕНЕНО: Fore импортирован явно
# import sys # Уже импортирован выше
import json
import urllib.request
import sqlite3
import threading
from pycoingecko import CoinGeckoAPI
import requests # ДОБАВЛЕНО: для отлова ошибок HTTP

from okex import okex # Убедись, что этот модуль установлен и настроен

cg = CoinGeckoAPI()

init() #colorama init
os.chdir(os.path.dirname(os.path.abspath(__file__))) # ИЗМЕНЕНО: Использован abspath для надежности

upd_interval = 300 # ИЗМЕНЕНО: Увеличен интервал обновления до 5 минут (300 секунд)
coins_list_refresh_interval = timedelta(hours=1) # ДОБАВЛЕНО: Интервал обновления списка монет (1 час)

# Глобальные переменные для кэширования
coins_list = [] # ДОБАВЛЕНО: кэш списка монет
last_coins_list_refresh_time = None # ДОБАВЛЕНО: время последнего обновления списка
coin_details_cache = {} # ДОБАВЛЕНО: кэш для деталей монет (чтобы не дергать get_coin_by_id часто)
coin_details_cache_ttl = timedelta(hours=6) # ДОБАВЛЕНО: время жизни кэша деталей

#Загрузка словарей=============================
reg_answers = []
def load_dic(dic):
	global reg_answers
	try: # ДОБАВЛЕНО: обработка ошибок чтения файла
		file = open('DICT/'+dic, 'r', encoding="utf8")
		dict_entry = {'reg': None, 'answers': []} # ИЗМЕНЕНО: Инициализация перед циклом
		current_answers = []
		current_reg = None

		for line in file:
			line = line.strip() # Убираем лишние пробелы и переводы строк
			if line.startswith('#') or not line: # Пропускаем комментарии и пустые строки
				if current_reg and current_answers:
					reg_answers.append({'reg': current_reg, 'answers': current_answers})
					current_answers = []
					current_reg = None
				continue

			if line.startswith('^'):
				# Если уже есть регулярка, сохраняем предыдущую запись
				if current_reg and current_answers:
					reg_answers.append({'reg': current_reg, 'answers': current_answers})

				try:
					current_reg = re.compile(line.lower())
					current_answers = []
				except re.error as e:
					print(f"Ошибка компиляции регулярного выражения в {dic}: {line} - {e}")
					current_reg = None # Сбрасываем, если ошибка
			elif current_reg: # Добавляем ответ только если есть активная регулярка
				current_answers.append(line)

		# Добавляем последнюю запись после окончания файла
		if current_reg and current_answers:
			reg_answers.append({'reg': current_reg, 'answers': current_answers})

		file.close()
		res = sum(1 for item in reg_answers if item['reg']) # Считаем только валидные регулярки
		print(f'Loaded: {dic} ({res} re)')
		return res
	except FileNotFoundError:
		print(f"Ошибка: Файл словаря не найден - DICT/{dic}")
		return 0
	except Exception as e:
		print(f"Ошибка при загрузке словаря {dic}: {e}")
		traceback.print_exc()
		return 0


def loadreg():
	global reg_answers
	reg_answers = []
	if not os.path.exists('DICT'):
		print("Папка DICT не найдена.")
		return
	dicts = os.listdir('DICT/')
	for file in dicts:
		if file.endswith('.dic'): # ИЗМЕНЕНО: проверка расширения
			load_dic(file)
		else: print(file+' не загружен (не .dic)')
loadreg()

#БАЗА=====================================
db = sqlite3.connect('moisha.db', check_same_thread=False)
cur = db.cursor()
# db.execute('VACUUM;') # VACUUM лучше делать не при каждом запуске, а по необходимости
try:
	#db.execute('''CREATE TABLE IF NOT EXISTS prices(time datetime, bcinfo, polo)''')
	db.execute('''CREATE TABLE IF NOT EXISTS chat_alerts(
				  id INTEGER PRIMARY KEY,
				  alerts TEXT
				)''') # ИЗМЕНЕНО: id как PRIMARY KEY
	db.execute('''CREATE TABLE IF NOT EXISTS settings(
				  setting TEXT PRIMARY KEY,
				  value TEXT
				)''') # ИЗМЕНЕНО: setting как PRIMARY KEY
except Exception as err:
	print("Ошибка при создании таблиц БД:")
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
		query = f"SELECT * FROM {table}"
		if cond:
			query += f" WHERE {cond}"
		cur.execute(query)
		data = cur.fetchall()
		return data
	except sqlite3.Error as err: # ИЗМЕНЕНО: Ловим ошибки SQLite
		print(f"Ошибка SQLite при получении данных из {table}: {err}")
		print(traceback.format_exc())
		return None # Возвращаем None при ошибке

def get_alerts(chat_id): # ИЗМЕНЕНО: принимаем chat_id
	result = get_data('chat_alerts', f'id = {chat_id}')
	if result:
		try:
			return json.loads(result[0]['alerts'])
		except (json.JSONDecodeError, IndexError) as e:
			print(f"Ошибка декодирования JSON или IndexError для чата {chat_id}: {e}")
			# Попытка исправить запись в БД
			try:
				cur = db.cursor()
				cur.execute('''UPDATE chat_alerts SET alerts = ? WHERE id = ?''', (json.dumps([]), chat_id))
				db.commit()
				print(f"Исправлена запись алертов для чата {chat_id}")
			except sqlite3.Error as db_err:
				print(f"Не удалось исправить запись алертов для чата {chat_id}: {db_err}")
			return []
	else:
		# Если записи нет, создаем ее
		try:
			cur = db.cursor()
			cur.execute('''INSERT INTO chat_alerts (id, alerts) VALUES (?, ?)''', (chat_id, json.dumps([])))
			db.commit()
			print(f"Создана запись алертов для нового чата {chat_id}")
			return []
		except sqlite3.Error as db_err:
			print(f"Не удалось создать запись алертов для чата {chat_id}: {db_err}")
			return [] # Возвращаем пустой список в случае ошибки

# ИЗМЕНЕНО: set_alert теперь принимает coin_id и current_price
def set_alert(msg, coin_id, porog=1, current_price=None):
	global db
	chat_id = msg['chat']['id']

	# Проверка на валидность ID уже должна быть сделана до вызова этой функции

	# Получаем текущую цену, только если она не передана (например, при ручном добавлении)
	if current_price is None:
		current_price = kurs(coin_id) # Вызов kurs теперь с ID
		if current_price is False: # Если не удалось получить курс
			say(msg, f'Не удалось получить текущий курс для {coin_id}. Алерт не добавлен.')
			return False # ДОБАВЛЕНО: Возвращаем False при ошибке

	cur = db.cursor()
	alerts = get_alerts(chat_id) # Используем исправленную get_alerts
	new_list = []
	done = False

	if alerts:
		for alert in alerts:
			# Сравниваем по ID, который теперь точно определен
			if alert['valute'] == coin_id:
				alert['time'] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
				alert['price'] = current_price # Используем переданную или только что полученную цену
				alert['porog'] = str(porog)
				done = True
			new_list.append(alert) # Всегда добавляем в новый список

	if not done:
		alert = {
			'time': datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
			'valute': coin_id, # Сохраняем ID
			'price': current_price,
			'porog': str(porog)
		}
		new_list.append(alert)

	try:
		cur.execute('''UPDATE chat_alerts SET alerts = ? WHERE id = ?''', (json.dumps(new_list), chat_id))
		db.commit()
		# Сообщаем об успехе только если это был ручной вызов команды
		if 'text' in msg:
			coin_sym = get_sym_by_id(coin_id) or coin_id # Пытаемся получить символ
			say(msg, f'Добавлен/обновлен алерт {coin_sym} ({coin_id}) с порогом {porog}%')
		return True # ДОБАВЛЕНО: Возвращаем True при успехе
	except sqlite3.Error as e:
		print(f"Ошибка SQLite при обновлении алертов для чата {chat_id}: {e}")
		if 'text' in msg:
			say(msg, 'Произошла ошибка при сохранении алерта.')
		return False # ДОБАВЛЕНО: Возвращаем False при ошибке


def remove_alert(msg, valute_str): # ИЗМЕНЕНО: принимает строку валюты
	global db
	chat_id = msg['chat']['id']
	coin_id = get_id_by_string(valute_str) # Определяем ID

	if not coin_id:
		say(msg, f'Не знаю такой валюты: {valute_str}')
		return

	cur = db.cursor()
	alerts = get_alerts(chat_id)
	new_list = []
	removed = False

	if alerts:
		for alert in alerts:
			if alert['valute'] == coin_id:
				removed = True
				# Просто не добавляем этот алерт в new_list
			else:
				new_list.append(alert)

		if removed:
			try:
				cur.execute('''UPDATE chat_alerts SET alerts = ? WHERE id = ?''', (json.dumps(new_list), chat_id))
				db.commit()
				coin_sym = get_sym_by_id(coin_id) or coin_id
				say(msg, f'Алерт {coin_sym} ({coin_id}) удалён.')
			except sqlite3.Error as e:
				print(f"Ошибка SQLite при удалении алерта для чата {chat_id}: {e}")
				say(msg, 'Произошла ошибка при удалении алерта.')
		else:
			say(msg, f'Алерт {get_sym_by_id(coin_id) or coin_id} ({coin_id}) не найден.')
	else:
		say(msg, 'Алерты для этого чата не настроены.')


def get_setting(setting):
	result = get_data('settings', f"setting = '{setting}'")
	if not result:
		return None # ИЗМЕНЕНО: Возвращаем None если настройки нет
	else:
		return result[0]['value']

def set_setting(setting, value):
	global db
	try:
		cur = db.cursor()
		# Используем INSERT OR REPLACE для упрощения
		cur.execute('''INSERT OR REPLACE INTO settings (setting, value) VALUES (?, ?)''', (setting, value))
		db.commit()
		return True
	except sqlite3.Error as err:
		print(f"Ошибка SQLite при установке настройки '{setting}': {err}")
		print(traceback.format_exc())
		return False

#как называть пользователя в консоли и логах
def user_name(msg):
	try:
		name = '@'+msg['from']['username']
	except KeyError: # ИЗМЕНЕНО: Ловим KeyError
		try:
			name = msg['from']['first_name']
			if 'last_name' in msg['from']: # Проверяем наличие last_name
				name += ' ' + msg['from']['last_name']
		except KeyError:
			try:
				name = msg['from']['first_name']
			except KeyError:
				name = f"cid:{msg['chat']['id']}" # ИЗМЕНЕНО: f-string
	return name

#собсна бот
class YourBot(telepot.Bot):
	def __init__(self, *args, **kwargs):
		super(YourBot, self).__init__(*args, **kwargs)
		self._answerer = telepot.helper.Answerer(self)
		self._message_with_inline_keyboard = None

	def on_chat_message(self, msg):
		content_type, chat_type, chat_id = telepot.glance(msg)

		# Логирование лучше делать в конце, после обработки, или использовать logging модуль
		# with open('tg.log', 'a', encoding="utf8") as log:
		# 	log.write(str(msg) + '\n')

		if content_type == 'new_chat_member':
			try:
				# ДОБАВЛЕНО: проверка, что новый участник - не сам бот
				if msg['new_chat_participant']['id'] != self.getMe()['id']:
					bot.sendSticker(chat_id, random.choice(['CAADAgADnAEAAr8cUgGqoY57iHWJagI','CAADAgADWAEAAr8cUgHoHDucQspSKwI']))
			except Exception as e:
				print(f"Ошибка при отправке стикера new_chat_member: {e}")
			return # ДОБАВЛЕНО: выходим после обработки new_chat_member

		if content_type != 'text':
			print(f'{datetime.now().strftime("%d.%m.%Y %H:%M:%S")} {content_type} {chat_type} {chat_id}')
			return

		try:
			print(Fore.RED + datetime.now().strftime("%d.%m.%Y %H:%M:%S") + f' {user_name(msg)}: ' + Fore.WHITE + msg['text'])
		except UnicodeEncodeError:
			print(f'{datetime.now().strftime("%d.%m.%Y %H:%M:%S")} {user_name(msg)}: [UnicodeEncodeError]')
		except Exception as e: # ДОБАВЛЕНО: ловим другие ошибки печати
			print(f"Ошибка вывода сообщения в консоль: {e}")

		process(msg) # Вызываем обработчик

		# ДОБАВЛЕНО: логирование после обработки
		try:
			with open('tg.log', 'a', encoding="utf8") as log:
				log.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
		except Exception as e:
			print(f"Ошибка записи в tg.log: {e}")


	def on_edited_chat_message(self, msg):
		# Можно добавить обработку измененных сообщений, если нужно
		pass

def say(msg, answer, silent=False):
	chat_id = msg['chat']['id']
	# обработка ключевых слов из словаря
	answer = answer.replace('[name]', user_name(msg))
	answer = answer.replace('[br]', '\n')

	if '[courses]' in answer:
		try:
			alerts = get_alerts(chat_id)
			stringg = ''
			alert_ids = [a['valute'] for a in alerts]
			if alert_ids:
				prices = get_prices_for_ids(alert_ids) # Получаем цены одним махом
				if prices:
					for alert in alerts:
						coin_id = alert['valute']
						coin_sym = get_sym_by_id(coin_id) or coin_id
						price = prices.get(coin_id, {}).get('usd', 'N/A') # Получаем цену из кэша
						if price != 'N/A':
							price_str = f"{price:.8f}".rstrip('0').rstrip('.') # Форматирование цены
							stringg += f'*{coin_sym} ({coin_id})*: {price_str}$\n'
						else:
							stringg += f'*{coin_sym} ({coin_id})*: Ошибка цены\n'
				else:
					stringg = 'Не удалось загрузить курсы.\n' # Ошибка получения цен
			else:
				stringg = 'Настройте алерты: /alert <валюта> [порог%]\n'

			answer = answer.replace('[courses]', stringg.strip()) # Убираем лишний перевод строки в конце
		except Exception as e:
			print(f"Ошибка при обработке [courses] для чата {chat_id}: {e}")
			traceback.print_exc()
			answer = answer.replace('[courses]', '*Ошибка при получении курсов*')

	# Отправка длинных сообщений
	try:
		if len(answer) > 4096:
			parts = []
			text = answer
			while len(text) > 0:
				if len(text) > 4096:
					part = text[:4096]
					first_lnbr = part.rfind('\n')
					if first_lnbr != -1:
						parts.append(part[:first_lnbr])
						text = text[first_lnbr:].lstrip() # Удаляем пробелы в начале следующей части
					else: # Если нет переносов строк, режем как есть
						parts.append(part)
						text = text[4096:]
				else:
					parts.append(text)
					break
			for part in parts:
				bot.sendMessage(chat_id, part, parse_mode='Markdown', disable_web_page_preview=True)
				time.sleep(0.1) # Небольшая пауза между частями
		else:
			bot.sendMessage(chat_id, answer, parse_mode='Markdown', disable_web_page_preview=True)

		if not silent:
			try:
				# Убираем Markdown для лога консоли
				log_answer = re.sub(r'[*_`\[\]()]', '', answer)
				print(Fore.GREEN + datetime.now().strftime("%d.%m.%Y %H:%M:%S") + f' Мойша (to {user_name(msg)}): ' + Fore.WHITE + log_answer)
			except UnicodeEncodeError:
				print(f'{datetime.now().strftime("%d.%m.%Y %H:%M:%S")} Мойша (to {user_name(msg)}): [UnicodeEncodeError]')
			except Exception as e:
				print(f"Ошибка вывода ответа Мойши в консоль: {e}")

	except telepot.exception.TelegramError as e: # ИЗМЕНЕНО: Ловим ошибки Telegram API
		print(f"Ошибка Telegram API при отправке сообщения в чат {chat_id}: {e}")
		# Можно добавить логику повторной отправки или уведомление админу
	except Exception as e:
		print(f"Неизвестная ошибка в функции say для чата {chat_id}: {e}")
		traceback.print_exc()

# ДОБАВЛЕНО: Функция для получения цен списком ID
def get_prices_for_ids(ids_list):
	if not ids_list:
		return {}
	unique_ids = list(set(ids_list)) # Убираем дубликаты
	ids_string = ','.join(unique_ids)
	try:
		# print(f"Запрос цен для: {ids_string}") # Для отладки
		data = cg.get_price(ids=ids_string, vs_currencies='usd')
		# print(f"Ответ цен: {data}") # Для отладки
		return data
	except (requests.exceptions.RequestException, ValueError, KeyError) as e: # Ловим ошибки сети, ValueError (429), KeyError
		if isinstance(e, ValueError) and '429' in str(e):
			print(f"{datetime.now().strftime('%H:%M:%S')} - Rate limit при получении цен!")
			time.sleep(10) # Небольшая пауза при rate limit
		else:
			print(f"Ошибка при получении цен для {len(unique_ids)} ID: {e}")
			# print(traceback.format_exc()) # Раскомментировать для детальной отладки
		return None # Возвращаем None при ошибке

# Основной цикл получения курсов и проверки алертов
def getcourses_loop():
	global valutes # valutes больше не используется глобально для формирования строки запроса
	try:
		print(f"{datetime.now().strftime('%H:%M:%S')} - Начало цикла getcourses")
		all_chat_alerts = get_data('chat_alerts')
		if all_chat_alerts is None: # Ошибка получения данных из БД
			print("Ошибка получения данных алертов из БД. Пропуск цикла.")
			return # Выходим из этой итерации

		unique_valute_ids = set()
		chat_alerts_map = {} # Сохраняем алерты по чатам для быстрой обработки

		for chat_data in all_chat_alerts:
			chat_id = chat_data['id']
			try:
				alerts = json.loads(chat_data['alerts'])
				chat_alerts_map[chat_id] = alerts # Сохраняем
				for alert in alerts:
					unique_valute_ids.add(alert['valute']) # Собираем уникальные ID
			except json.JSONDecodeError:
				print(f"Ошибка декодирования JSON алертов для чата {chat_id}. Пропуск чата.")
				continue # Пропускаем битые данные

		if not unique_valute_ids:
			print("Нет настроенных алертов. Пропуск запроса цен.")
			return # Выходим, если нет алертов

		# Получаем все цены одним запросом
		current_prices = get_prices_for_ids(list(unique_valute_ids))

		if current_prices is None:
			print("Не удалось получить цены. Пропуск обработки алертов.")
			return # Выходим, если не получили цены

		# Обрабатываем алерты
		do_chat_alerts(chat_alerts_map, current_prices)

		# Проверяем новые монеты (вызываем реже, чем getcourses)
		# Можно сделать отдельный таймер для recheck_list
		recheck_list()
		print(f"{datetime.now().strftime('%H:%M:%S')} - Конец цикла getcourses")

	except Exception as err:
		print(f"Критическая ошибка в цикле getcourses_loop: {err}")
		print(traceback.format_exc())
	finally: # ДОБАВЛЕНО: Перезапуск таймера в блоке finally
		# Перезапускаем таймер только если бот все еще должен работать (run=True)
		if run:
			getcourses_timer = threading.Timer(upd_interval, getcourses_loop)
			getcourses_timer.name = 'getcourses_timer'
			getcourses_timer.start()
		else:
			print("Бот остановлен, таймер getcourses_loop не перезапущен.")

# ИЗМЕНЕНО: valid_valute теперь просто проверяет через get_id_by_string
def valid_valute(valute_str):
	return bool(get_id_by_string(valute_str)) # True если ID найден, иначе False

# ИЗМЕНЕНО: kurs теперь принимает ID и использует кеш цен если возможно
def kurs(coin_id):
	# Эта функция теперь в основном для ручного запроса курса,
	# т.к. для алертов цены берутся из get_prices_for_ids
	if not coin_id: # Проверка если передали невалидный id
		return False
	try:
		# print(f"Запрос kurs для: {coin_id}") # Отладка
		result = cg.get_price(ids=coin_id, vs_currencies='usd')
		# print(f"Ответ kurs: {result}") # Отладка
		if coin_id in result and 'usd' in result[coin_id]:
			return float(result[coin_id]['usd'])
		else:
			print(f"Неожиданный формат ответа от get_price для {coin_id}: {result}")
			return False
	except (requests.exceptions.RequestException, ValueError, KeyError) as e:
		if isinstance(e, ValueError) and '429' in str(e):
			print(f"{datetime.now().strftime('%H:%M:%S')} - Rate limit при запросе kurs для {coin_id}!")
			time.sleep(10)
		else:
			print(f"Ошибка при получении курса для {coin_id}: {e}")
		return False

def tonmine (power_consumption = 0.1, power_price = 5):
	try:
		request = urllib.request.Request('https://ton-reports-24d2v.ondigitalocean.app/report/pool-profitability', headers={'User-Agent': 'Mozilla/5.0'}) # ДОБАВЛЕНО: User-Agent
		response = urllib.request.urlopen(request, timeout=10) # ДОБАВЛЕНО: timeout
		json_received = (response.read()).decode('utf-8', errors='ignore')
		p = json.loads(json_received)
		profitability = int(p['profitabilityPerGh'])/(10**9)
		net_cost = (power_consumption*24*power_price)/profitability if profitability else 0 # ДОБАВЛЕНО: проверка деления на ноль
		return f'avg prof: {profitability:.8f} TON/Gh/day\nСебестоимость: {net_cost:.2f} руб/TON' # ИЗМЕНЕНО: f-string и формат вывода
	except urllib.error.URLError as e:
		print(f"Ошибка сети при запросе Ton profitability: {e}")
		return "Ошибка получения данных о доходности майнинга (сеть)."
	except (json.JSONDecodeError, KeyError, ValueError) as e:
		print(f"Ошибка обработки данных Ton profitability: {e}")
		return "Ошибка получения данных о доходности майнинга (формат)."
	except Exception as e:
		print(f"Неизвестная ошибка в tonmine: {e}")
		traceback.print_exc()
		return "Неизвестная ошибка при расчете доходности майнинга."


# ДОБАВЛЕНО: функция для получения деталей монеты с кэшированием
def get_coin_details_cached(coin_id):
	global coin_details_cache
	now = datetime.now()
	if coin_id in coin_details_cache:
		data, timestamp = coin_details_cache[coin_id]
		if now - timestamp < coin_details_cache_ttl:
			# print(f"Взят из кэша: {coin_id}") # Отладка
			return data # Возвращаем из кэша

	# Если нет в кэше или кэш устарел
	try:
		print(f"Запрос деталей для: {coin_id}") # Отладка
		q = cg.get_coin_by_id(coin_id, localization='false', tickers='false', market_data='true', community_data='false', developer_data='false', sparkline='false') # Оптимизация запроса
		coin_details_cache[coin_id] = (q, now) # Сохраняем в кэш
		return q
	except (requests.exceptions.RequestException, ValueError) as e: # Ловим ошибки сети и ValueError (429)
		if isinstance(e, ValueError) and '429' in str(e):
			print(f"{datetime.now().strftime('%H:%M:%S')} - Rate limit при запросе деталей {coin_id}!")
			time.sleep(10)
		elif isinstance(e, ValueError) and 'invalid coin_id' in str(e).lower():
			print(f"CoinGecko не нашел ID: {coin_id}")
			return {'error': f"ID {coin_id} не найден в CoinGecko."} # Возвращаем маркер ошибки
		else:
			print(f"Ошибка при получении деталей для {coin_id}: {e}")
		# Удаляем из кэша при ошибке, чтобы попробовать снова позже
		if coin_id in coin_details_cache:
			del coin_details_cache[coin_id]
		return None # Возвращаем None при ошибке

# ИЗМЕНЕНО: get_info_from_id использует кэшированную функцию
def get_info_from_id(coin_id):
	q = get_coin_details_cached(coin_id)

	if q is None:
		return f"Ошибка получения данных для ID: {coin_id}. Возможно, проблемы с API CoinGecko или rate limit."
	if 'error' in q: # Проверяем маркер ошибки от get_coin_details_cached
		return q['error']
	if not isinstance(q, dict): # Доп. проверка на всякий случай
		print(f"Неожиданный тип данных от get_coin_details_cached для {coin_id}: {type(q)}")
		return f"Неожиданный формат данных для ID: {coin_id}"

	answ = f"*ID:* {q.get('id', 'N/A')}\n" \
		   f"*SYM:* {q.get('symbol', 'N/A').upper()}\n" \
		   f"*Name:* {q.get('name', 'N/A')}\n"

	# Цена из market_data
	price_rub = q.get('market_data', {}).get('current_price', {}).get('rub')
	price_usd = q.get('market_data', {}).get('current_price', {}).get('usd')
	if price_rub:
		answ += f"*Цена:* ~{price_rub:.2f} руб.\n"
	elif price_usd:
		answ += f"*Price:* ~{price_usd:.4f} $.\n"
	else:
		answ += "*Нет данных по цене.*\n"

	# Категории
	categories = q.get('categories')
	if categories:
		valid_cats = [cat for cat in categories if cat] # Убираем None
		if valid_cats:
			answ += "*categories:* " + ', '.join(valid_cats) + '\n'

	# Ссылки
	links_data = q.get('links', {})
	if links_data:
		links_str = ""
		# Главный сайт
		homepage = [h for h in links_data.get('homepage', []) if h]
		if homepage: links_str += "*Homepage:* " + ', '.join(homepage) + "\n"
		# Эксплореры
		explorers = [e for e in links_data.get('blockchain_site', []) if e]
		if explorers: links_str += "*Explorers:* " + ', '.join(explorers) + "\n"
		# Исходный код
		repos = links_data.get('repos_url', {})
		github_repos = [g for g in repos.get('github', []) if g]
		bitbucket_repos = [b for b in repos.get('bitbucket', []) if b]
		if github_repos: links_str += "*GitHub:* " + '\n'.join(github_repos) + "\n"
		if bitbucket_repos: links_str += "*Bitbucket:* " + '\n'.join(bitbucket_repos) + "\n"
		# Twitter
		twitter = links_data.get('twitter_screen_name')
		if twitter: links_str += f"*Twitter:* [@{twitter}](https://twitter.com/{twitter})\n"
		# Telegram
		telegram = links_data.get('telegram_channel_identifier')
		if telegram: links_str += f"*Telegram:* @{telegram}\n"
		# Reddit
		reddit = links_data.get('subreddit_url')
		if reddit: links_str += f"*Reddit:* {reddit}\n"

		if links_str:
			answ += "*Links:*\n" + links_str.replace('_', r'\_') # Экранируем подчеркивания для Markdown

	# Tickers (Биржи) - убрано из запроса get_coin_by_id для оптимизации
	# Если нужно, можно добавить отдельный запрос cg.get_coin_ticker_by_id(coin_id)

	return answ


def process (msg):
	global reg_answers, pause, db, run, coins_list # pause не используется
	answer = ''
	# Проверка на наличие текста сообщения
	if 'text' not in msg:
		return # Игнорируем сообщения без текста (фото, стикеры и т.д.)

	# Убираем упоминание бота
	bot_username = bot.getMe().get('username')
	if bot_username:
		msg['text'] = msg['text'].replace(f'@{bot_username}', '').strip()
	else:
		print("Не удалось получить username бота") # Ошибка, если getMe не сработал

	# Проверка на пустую строку после удаления упоминания
	if not msg['text']:
		return

	command = msg['text'].lower().split()[0] # Получаем первое слово как команду
	args = msg['text'].split()[1:] # Остальное - аргументы

	# Обработка команд
	if command == '/alert' or command == '/alerts':
		if not args: # /alert или /alerts без аргументов
			if command == '/alerts':
				# Показать текущие алерты
				chat_id = msg['chat']['id']
				alerts = get_alerts(chat_id)
				stringg = ''
				if alerts:
					stringg += '*Текущие алерты:*\n'
					for alert in alerts:
						coin_id = alert['valute']
						coin_sym = get_sym_by_id(coin_id) or coin_id
						porog = alert['porog']
						stringg += f'*{coin_sym} ({coin_id})* - {porog}%\n'
				else:
					stringg = 'Алерты не настроены. Используйте:\n`/alert <валюта> [порог%]`'
				say(msg, stringg.strip())
			else: # /alert без аргументов
				say (msg, "Использование:\n`/alert <валюта> [порог%]`\nНапример:\n`/alert bitcoin 5` (оповещение при изменении на 5%)\n`/alert eth` (оповещение при изменении на 1%)")
			return

		valute_str = args[0]
		porog = 1 # Порог по умолчанию
		if len(args) > 1:
			try:
				porog = int(args[1])
				if porog <= 0:
					say(msg, "Порог должен быть положительным числом.")
					return
			except ValueError:
				say(msg, "Неверное значение порога, введите целое число процентов.")
				return

		coin_id = get_id_by_string(valute_str)
		if not coin_id:
			say(msg, f'Не знаю такой валюты: "{valute_str}". Попробуйте `/search {valute_str}`.')
			return

		# Вызываем set_alert (он сам получит цену, т.к. current_price не передаем)
		set_alert(msg, coin_id, porog)
		return # Важно выйти после обработки команды

	elif command == '/noalert' or command == '/noalerts':
		if command == '/noalerts': # Удалить все алерты
			chat_id = msg['chat']['id']
			try:
				cur = db.cursor()
				cur.execute('''UPDATE chat_alerts SET alerts = ? WHERE id = ?''', (json.dumps([]), chat_id))
				db.commit()
				say(msg, 'Все алерты удалены.')
			except sqlite3.Error as e:
				print(f"Ошибка SQLite при удалении всех алертов для чата {chat_id}: {e}")
				say(msg, 'Произошла ошибка при удалении алертов.')
		elif not args: # /noalert без аргументов
			say(msg, 'Использование: `/noalert <валюта>`')
		else:
			valute_str = args[0]
			remove_alert(msg, valute_str) # remove_alert сам найдет ID
		return

	elif command == '/newalerts':
		chat_id = msg['chat']['id']
		na_json = get_setting('newalerts')
		newalerts_list = []
		if na_json:
			try:
				newalerts_list = json.loads(na_json)
				if not isinstance(newalerts_list, list): # Проверка типа
					print("Ошибка: newalerts в БД не является списком. Сброс.")
					newalerts_list = []
			except json.JSONDecodeError:
				print("Ошибка декодирования newalerts из БД. Сброс.")
				newalerts_list = []

		if chat_id not in newalerts_list:
			newalerts_list.append(chat_id)
			if set_setting('newalerts', json.dumps(newalerts_list)):
				say(msg, 'Буду уведомлять о новых койнах в этом чате.')
			else:
				say(msg, 'Ошибка сохранения настройки уведомлений.')
		else:
			say(msg, 'Уже уведомляю этот чат о новых койнах.')
		return

	elif command == '/nonewalerts':
		chat_id = msg['chat']['id']
		na_json = get_setting('newalerts')
		if na_json:
			try:
				newalerts_list = json.loads(na_json)
				if not isinstance(newalerts_list, list):
					print("Ошибка: newalerts в БД не является списком.")
					say(msg, 'Не удалось прочитать настройку (ошибка формата).')
					return
				if chat_id in newalerts_list:
					newalerts_list.remove(chat_id)
					if set_setting('newalerts', json.dumps(newalerts_list)):
						say(msg, 'Больше не буду уведомлять о новых койнах в этом чате.')
					else:
						say(msg, 'Ошибка сохранения настройки уведомлений.')
				else:
					say(msg, 'Я и так не уведомляю этот чат о новых койнах.')
			except json.JSONDecodeError:
				print("Ошибка декодирования newalerts из БД при удалении.")
				say(msg, 'Не удалось прочитать настройку (ошибка декодирования).')
		else:
			say(msg, 'Я и так не уведомляю этот чат о новых койнах.')
		return

	elif command == '/search':
		if not args:
			say (msg, "Что искать? `/search <запрос>`")
			return
		query = ' '.join(args).lower()
		refresh_coins_list_if_needed() # Обновляем список перед поиском, если нужно
		if not coins_list:
			say(msg, "Не удалось загрузить список монет для поиска. Попробуйте позже.")
			return

		found = []
		# Ищем по ID, символу и имени
		for coin in coins_list:
			if query in coin.get('id', '').lower() or \
			   query in coin.get('symbol', '').lower() or \
			   query in coin.get('name', '').lower():
				if coin not in found:
					found.append(coin)
					if len(found) >= 20: # Ограничиваем вывод
						break

		if found:
			answ = '*Результаты поиска:*\n'
			for i in found:
				# Форматируем вывод: Имя (СИМВОЛ) - ID
				name = i.get('name', 'N/A')
				symbol = i.get('symbol', 'N/A').upper()
				coin_id = i.get('id', 'N/A')
				answ += f"*{name}* ({symbol}) - `{coin_id}`\n" # Используем Markdown для ID
			if len(found) >= 20:
				answ += "\n_(Показаны первые 20 совпадений)_"
			say(msg, answ)
		else:
			say(msg, f'Ничего не найдено по запросу "{query}".')
		return

	elif command == '/info':
		if not args:
			say (msg, "Информацию по какому ID монеты показать? `/info <id>`")
			return
		coin_id_or_sym = args[0].lower()
		coin_id = get_id_by_string(coin_id_or_sym) # Ищем ID по строке

		if not coin_id:
			say(msg, f'Не удалось найти монету по "{coin_id_or_sym}". Попробуйте сначала `/search {coin_id_or_sym}`.')
			return

		try:
			answ = get_info_from_id(coin_id) # Использует кэшированную функцию
			# Фильтр булшита можно вызывать здесь, если нужно
			# fb = filter_bullshit(answ)
			# if fb: ...
			say(msg, answ)
		except Exception as err:
			print(f"Ошибка при получении /info для {coin_id}: {err}")
			print(traceback.format_exc())
			say(msg, f'Ошибка обработки данных для ID {coin_id}.')
		return

	elif command == '/mine':
		try:
			# Можно добавить аргументы для мощности и цены
			power_consumption = 0.1
			power_price = 5
			if len(args) >= 2:
				try:
					power_consumption = float(args[0].replace(',', '.'))
					power_price = float(args[1].replace(',', '.'))
				except ValueError:
					say(msg, "Неверный формат аргументов. Используйте: `/mine [кВт/Гх] [руб/кВтч]`")
					return
			elif len(args) == 1:
				say(msg, "Нужно два аргумента: `/mine [кВт/Гх] [руб/кВтч]` или ни одного для значений по умолчанию.")
				return

			mine_info = tonmine(power_consumption, power_price)
			say(msg, f'{mine_info}\n_(при {power_consumption} кВт/Гх и {power_price} руб/кВтч)_')
		except Exception as err:
			print(f"Ошибка в команде /mine: {err}")
			print(traceback.format_exc())
			say(msg, "Ошибка при расчете доходности майнинга.")
		return

	elif command == '/reload':
		try:
			usercheck = msg.get('from', {}).get('username')
			if usercheck != "Brakhma": # ИЗМЕНЕНО: Безопасное получение username
				say(msg, "Permission denied!")
				print (f'{user_name(msg)} TRIES TO RELOAD!')
				return
			say(msg, "Перезагружаюсь...")
			os.system("git pull") # Сначала обновим код
			stopthreads() # Останавливаем таймеры
			run = False # Сигнал основному циклу для завершения
		except Exception as err:
			print("Ошибка при выполнении /reload:")
			print(traceback.format_exc())
			say(msg, "Ошибка при перезагрузке.")
		return

	# Команды фонда и акций - оставляю без изменений, но добавил проверки
	elif command == '/fund':
		f = get_setting('fund_shares')
		if not f:
			say(msg, "Данные о долях фонда не найдены.")
			return
		try:
			shares = json.loads(f)
			if not isinstance(shares, list):
				say(msg, "Ошибка формата данных о долях фонда.")
				return
		except json.JSONDecodeError:
			say(msg, "Ошибка чтения данных о долях фонда.")
			return

		shares_total = 0
		my_cut_amount = 0 # ИЗМЕНЕНО: имя переменной
		user_id = msg['from']['id']

		for item in shares:
			# Ожидаем формат [user_id_str, share_amount_str]
			if isinstance(item, list) and len(item) == 2:
				try:
					share_user_id = int(item[0])
					share_amount = int(item[1])
					shares_total += share_amount
					if share_user_id == user_id:
						my_cut_amount = share_amount
				except (ValueError, TypeError):
					print(f"Неверный формат доли в fund_shares: {item}")
					continue # Пропускаем некорректную запись
			else:
				print(f"Неверный формат записи в fund_shares: {item}")
				continue

		if shares_total == 0: # Обработка, если нет валидных долей
			say(msg, "В фонде нет валидных долей.")
			return

		if my_cut_amount == 0:
			say(msg, 'Таки тьфу на тебя, твоей доли нет.')
			return

		perc = round((my_cut_amount / shares_total) * 100, 2)

		fund_str = '*Структура фонда:*\n'
		total_eq_usd = 0 # ИЗМЕНЕНО: имя переменной

		try:
			# Убедись, что ключи API для okex заданы в settings.py или переменных окружения
			from settings import okex_apikey, okex_secret, okex_passphrase # ДОБАВЛЕНО: импорт ключей
			ok = okex(api_key=okex_apikey, api_secret=okex_secret, passphrase=okex_passphrase)
			# Получаем балансы активов
			balances = ok.get_balances() # TBD: Нужен правильный метод v5 API
			# !!! ВАЖНО: Код ниже для OKEx v3 API, нужно адаптировать для v5 !!!
			# Примерный аналог для v5:
			# account_api = Account.AccountAPI(okex_apikey, okex_secret, okex_passphrase, False, "0")
			# result = account_api.get_account_balance()
			# balances = result.get('data', [{}])[0].get('details', [])
			# for i in balances:
			#	total_eq_usd += float(i.get('eqUsd', 0))
			#	fund_str += f"{i.get('ccy', 'N/A')}: {i.get('cashBal', 'N/A')}\n"
			fund_str += "ОШИБКА: Функция /fund требует обновления для OKEx v5 API\n" # Заглушка

			# Получение ордеров (тоже требует адаптации для v5)
			# trade_api = Trade.TradeAPI(...)
			# orders_result = trade_api.get_order_list(...)
			# orders = orders_result.get('data', [])
			# if orders: fund_str+='*Открытые ордера:*\n'
			# for i in orders:
			#	fund_str+=f"{i['instId']} {i['side']} {i['sz']} x {i['px']}\n"

		except ImportError:
			fund_str += "ОШИБКА: Не найдены ключи API для OKEx в settings.py\n"
		except NameError:
			fund_str += "ОШИБКА: Не найдены ключи API для OKEx (NameError)\n"
		except Exception as e:
			fund_str += f"Ошибка при получении данных с OKEx: {e}\n"
			print(f"Ошибка OKEx: {e}")
			traceback.print_exc()


		fund_str += '*Примерная оценка активов (USD):*\n' # Оставим пока только USD
		fund_str += f'~{total_eq_usd:.2f} $\n'

		# Конвертация в рубли (требует рабочего cg.get_price)
		usd_to_rub_rate = None
		try:
			req = cg.get_price(ids='tether', vs_currencies='rub') # tether=usdt
			usd_to_rub_rate = float(req['tether']['rub'])
			total_eq_rub = total_eq_usd * usd_to_rub_rate
			fund_str += f'~{total_eq_rub:.2f} ₽\n'
		except Exception as e:
			print(f"Ошибка получения курса USDT/RUB: {e}")
			fund_str += "(Не удалось получить курс RUB)\n"


		fund_str += '*Твоя доля:*\n'
		my_cut_usd = total_eq_usd * (my_cut_amount / shares_total)
		fund_str += f'~{my_cut_usd:.2f} $ ({perc}%)\n'
		if usd_to_rub_rate:
			my_cut_rub = total_eq_rub * (my_cut_amount / shares_total)
			fund_str += f'~{my_cut_rub:.2f} ₽ ({perc}%)\n'

		fund_str += '\n\*без учёта комиссий за конвертацию и вывод.'
		say(msg, fund_str)
		return

	elif command == '/add_shares':
		try:
			usercheck = msg.get('from', {}).get('username')
			if usercheck != "Brakhma":
				say(msg, "Permission denied!")
				return
			if not args or len(args) < 2: # Проверка аргументов
				say(msg, "Использование: `/add_shares <user_id> <количество>`")
				return

			new_user_id_str = args[0]
			new_shares_amount_str = args[1]

			# Валидация входных данных
			try:
				new_user_id = int(new_user_id_str)
				new_shares_amount = int(new_shares_amount_str)
				if new_shares_amount <= 0:
					say(msg, "Количество долей должно быть положительным.")
					return
			except ValueError:
				say(msg, "ID пользователя и количество долей должны быть числами.")
				return

			f = get_setting('fund_shares')
			shares = []
			if f:
				try:
					shares = json.loads(f)
					if not isinstance(shares, list):
						say(msg, "Ошибка формата данных о долях в БД. Создан новый список.")
						shares = []
				except json.JSONDecodeError:
					say(msg, "Ошибка чтения данных о долях из БД. Создан новый список.")
					shares = []

			# TBD: Логика пересчета долей при добавлении нового участника
			# Сейчас просто добавляем или обновляем

			found_user = False
			for i in range(len(shares)):
				# Проверяем формат элемента списка
				if isinstance(shares[i], list) and len(shares[i]) == 2:
					try:
						if int(shares[i][0]) == new_user_id:
							# Обновляем долю существующего пользователя
							shares[i][1] = str(int(shares[i][1]) + new_shares_amount)
							found_user = True
							break
					except (ValueError, TypeError):
						# Пропускаем или удаляем некорректные записи
						print(f"Некорректная запись в shares при обновлении: {shares[i]}")
						# можно добавить shares.pop(i) или continue
						continue
				else:
					print(f"Некорректный формат элемента в shares: {shares[i]}")
					continue


			if not found_user:
				# Добавляем нового пользователя
				shares.append([str(new_user_id), str(new_shares_amount)])

			if set_setting('fund_shares', json.dumps(shares)):
				say(msg, f'Добавлено/обновлено {new_shares_amount} долей для user_id {new_user_id}.\nТекущие доли: {shares}')
			else:
				say(msg, 'Ошибка сохранения данных о долях.')
			return
		except Exception as err:
			say(msg, f'Ошибка при добавлении долей: {err}')
			print(traceback.format_exc())
		return

	# === Обработка конвертера и общих паттернов ===

	# Конвертер (улучшенная регулярка)
	# (число)[пробел](валюта1)[пробел]to[пробел](валюта2)
	# Число может быть целым или дробным (через точку или запятую)
	converter_match = re.match(r'^((\d+([.,]\d+)?))\s+([a-zA-Z0-9.-]+)\s+to\s+([a-zA-Z0-9.-]+)$', msg['text'].lower())
	if converter_match:
		amount_str = converter_match.group(1).replace(',', '.')
		cur1_str = converter_match.group(4)
		cur2_str = converter_match.group(5)
		try:
			amount = float(amount_str)
			answ = converter(amount, cur1_str, cur2_str) # Передаем аргументы в converter
			say(msg, answ)
		except ValueError:
			say(msg, "Неверный формат числа.")
		except Exception as e:
			print(f"Ошибка в конвертере: {e}")
			traceback.print_exc()
			say(msg, "Ошибка при конвертации валют.")
		return # Выходим после обработки конвертера

	# Обработка регулярок из словаря
	for pair in reg_answers:
		# Проверяем, скомпилировалась ли регулярка
		if pair['reg'] is None:
			continue
		# Используем search вместо match для поиска в любом месте строки
		if pair['reg'].search(msg['text'].lower()):
			if pair['answers']: # Убедимся что есть ответы
				answer = random.choice(pair['answers'])
				say(msg, answer)
				return # Выходим после первого совпадения

	# Если ни одна команда или регулярка не подошла (можно добавить ответ по умолчанию)
	# print(f"Неизвестная команда или текст: {msg['text']}")


# --- Инициализация и Запуск ---

try:
	from settings import TOKEN, okex_apikey, okex_secret, okex_passphrase # Импортируем токен и ключи
except ImportError:
	print("Ошибка: Не найден файл settings.py или в нем отсутствуют TOKEN/ключи OKEx.")
	TOKEN = input('Введите Bot-API токен: ')
	# Ключи OKEx тоже нужно будет как-то получить или задать
	okex_apikey = None
	okex_secret = None
	okex_passphrase = None
	# Можно добавить логику сохранения в файл, если нужно

# Прокси больше не используется стандартно в telepot, если нужен - нужна другая библиотека или настройка requests
# if proxxx: telepot.api.set_proxy('https://'+proxxx)

# ДОБАВЛЕНО: Проверка токена
if not TOKEN:
	print("Токен бота не задан. Выход.")
	sys.exit(1)

bot = YourBot(TOKEN)
print (Fore.YELLOW + bot.getMe()['first_name']+' (@'+bot.getMe()['username']+')'+Fore.WHITE)

def stopthreads():
	print("Остановка фоновых потоков...")
	for thing in threading.enumerate():
		if isinstance(thing, threading.Timer):
			print(f"Отмена таймера: {thing.name}")
			thing.cancel()
	# Даем немного времени таймерам на завершение
	time.sleep(1)
	# Проверяем снова
	remaining_timers = [t.name for t in threading.enumerate() if isinstance(t, threading.Timer) and t.is_alive()]
	if remaining_timers:
		print(f"Не удалось остановить таймеры: {remaining_timers}")
	else:
		print ("Все таймеры успешно отменены.")

def printthreads():
	strr=''
	print("--- Активные потоки ---")
	for thing in threading.enumerate():
		strr += str(thing)+'\n'
	print(strr.strip())
	print("-----------------------")
	return strr

# ИЗМЕНЕНО: do_chat_alerts принимает карту алертов и текущие цены
def do_chat_alerts(chat_alerts_map, current_prices):
	if not current_prices: # Если цены получить не удалось
		return

	print(f"{datetime.now().strftime('%H:%M:%S')} - Обработка алертов для {len(chat_alerts_map)} чатов...")
	active_alerts_count = 0 # Счетчик активных алертов для отладки

	for chat_id, alerts in chat_alerts_map.items():
		if not alerts: continue # Пропускаем чаты без алертов

		msg = {'chat': {'id': chat_id}} # Создаем шаблон сообщения для say и set_alert

		alerts_to_update = [] # Собираем алерты, которые нужно обновить в БД

		for alert in alerts:
			coin_id = alert['valute']
			active_alerts_count += 1

			if coin_id not in current_prices or 'usd' not in current_prices[coin_id]:
				# print(f"Нет текущей цены для {coin_id} в чате {chat_id}") # Отладка
				continue # Пропускаем, если нет цены для этой монеты

			try:
				old_price = float(alert['price'])
				old_time_str = alert['time']
				porog = float(alert['porog'])
				current_price = float(current_prices[coin_id]['usd'])

				if current_price == 0: continue # Пропускаем нулевые цены

				change_percent = ((current_price - old_price) / old_price) * 100 if old_price != 0 else (100 if current_price > 0 else 0)

				# Проверяем порог
				if abs(change_percent) >= porog:
					# Формируем сообщение
					if change_percent > 0:
						chg_str = '▲'#random.choice(['вырос', 'пульнул', 'отрос', 'поднялся'])
					else:
						chg_str = '▼'#random.choice(['упал', 'дропнулся', 'рухнул', 'опустился'])

					# Расчет времени
					try:
						old_time = datetime.strptime(old_time_str, "%d.%m.%Y %H:%M:%S")
						timediff = datetime.now() - old_time
						if timediff.days > 0:
							strtd = f'{timediff.days} д.'
						elif (timediff.seconds // 3600) > 0:
							strtd = f'{timediff.seconds // 3600} ч.'
						else:
							mins = (timediff.seconds // 60) % 60
							strtd = f'{max(1, mins)} мин.' # Показываем минимум 1 минуту
					except ValueError:
						strtd = 'недавно' # Если ошибка парсинга времени

					coin_sym = get_sym_by_id(coin_id) or coin_id
					# Форматируем цену: убираем лишние нули
					price_str = f"{current_price:.8f}".rstrip('0').rstrip('.')
					old_price_str = f"{old_price:.8f}".rstrip('0').rstrip('.')

					res_str = f"{coin_sym} {abs(change_percent):.1f}% {chg_str} за {strtd}\n" \
							  f"`{old_price_str}` → `{price_str}` USD"

					# Отправляем уведомление
					say(msg, res_str)

					# Помечаем алерт для обновления в БД с новой ценой и временем
					alerts_to_update.append({'chat_id': chat_id, 'coin_id': coin_id, 'porog': porog, 'current_price': current_price})

			except (ValueError, KeyError, TypeError) as e:
				print(f"Ошибка обработки алерта {alert} для чата {chat_id}: {e}")
				# Можно добавить логику удаления "битого" алерта из БД
				continue

		# Обновляем сработавшие алерты в БД после цикла по алертам чата
		if alerts_to_update:
			print(f"Обновление {len(alerts_to_update)} сработавших алертов для чата {chat_id}...")
			updated_count = 0
			failed_count = 0
			# Используем временный объект msg, т.к. оригинальный мог быть от пользователя
			update_msg = {'chat': {'id': chat_id}}
			for update_data in alerts_to_update:
				# Передаем текущую цену, чтобы избежать лишнего запроса в set_alert
				if set_alert(update_msg, update_data['coin_id'], update_data['porog'], update_data['current_price']):
					updated_count += 1
				else:
					failed_count += 1
			print(f"Обновлено: {updated_count}, Ошибки: {failed_count} для чата {chat_id}")

	print(f"{datetime.now().strftime('%H:%M:%S')} - Обработано {active_alerts_count} активных алертов.")

# ДОБАВЛЕНО: Функция для обновления глобального списка монет
def refresh_coins_list_if_needed(force_refresh=False):
	global coins_list, last_coins_list_refresh_time
	now = datetime.now()
	if force_refresh or not last_coins_list_refresh_time or (now - last_coins_list_refresh_time > coins_list_refresh_interval):
		print(f"{datetime.now().strftime('%H:%M:%S')} - Обновление списка монет...")
		try:
			new_coins_list = cg.get_coins_list()
			if new_coins_list: # Проверка, что список не пустой
				coins_list = new_coins_list
				last_coins_list_refresh_time = now
				set_setting('coins_list', json.dumps(coins_list)) # Сохраняем в БД
				print(f"Список монет обновлен ({len(coins_list)} монет) и сохранен в БД.")
				return True
			else:
				print("Ошибка: Получен пустой список монет от API.")
				return False
		except (requests.exceptions.RequestException, ValueError) as e: # Ловим ошибки сети и ValueError (429)
			if isinstance(e, ValueError) and '429' in str(e):
				print(f"{datetime.now().strftime('%H:%M:%S')} - Rate limit при обновлении списка монет!")
				# Не обновляем время, попробуем в следующий раз
			else:
				print(f"Ошибка при обновлении списка монет: {e}")
			return False
	else:
		# print("Используется кэшированный список монет.") # Отладка
		return True # Список актуален

      
def get_id_by_string(search_string):
    global coins_list, cg # Убедимся, что cg доступен
    search_string_lower = search_string.lower()

    # --- Шаг 0: Хардкод для самых частых конфликтов ---
    if search_string_lower == 'btc':
        # print("DEBUG: Хардкод 'btc' -> 'bitcoin'") # Отладка
        return 'bitcoin'
    # Можно добавить другие, например:
    # if search_string_lower == 'eth':
    #    return 'ethereum'

    # --- Проверка и загрузка основного списка монет ---
    if not coins_list:
        # ... (код загрузки coins_list, как и раньше) ...
        print("Предупреждение: get_id_by_string вызван с пустым кэшем coins_list.")
        saved_list_json = get_setting('coins_list')
        if saved_list_json:
            try:
                coins_list = json.loads(saved_list_json)
                print("Загружен список монет из БД.")
            except json.JSONDecodeError:
                print("Ошибка декодирования списка монет из БД.")
                if not refresh_coins_list_if_needed(force_refresh=True): return False
        else:
            if not refresh_coins_list_if_needed(force_refresh=True): return False

    # --- Шаг 1: Поиск по ID (точное совпадение) ---
    for coin in coins_list:
        coin_id = coin.get('id')
        if coin_id and coin_id.lower() == search_string_lower:
            # print(f"DEBUG: Найдено по ID: {coin['id']}") # Отладка
            return coin['id']

    # --- Шаг 2: Поиск по символу (точное совпадение) ---
    matches_coins = []
    for coin in coins_list:
        coin_symbol = coin.get('symbol')
        coin_id = coin.get('id')
        if coin_symbol and coin_id and coin_symbol.lower() == search_string_lower:
            matches_coins.append(coin)

    # --- Шаг 3: Обработка результатов поиска по символу ---
    if len(matches_coins) == 1:
        # Однозначное совпадение по символу
        # print(f"DEBUG: Найдено по символу (1): {matches_coins[0]['id']}") # Отладка
        return matches_coins[0]['id']
    elif len(matches_coins) > 1:
        # Несколько совпадений - РАЗРЕШЕНИЕ КОНФЛИКТА
        conflicting_ids = [c['id'] for c in matches_coins]
        setting_key = f"symbol_resolution_{search_string_lower}"
        print(f"Обнаружен конфликт символа '{search_string_lower}': {conflicting_ids}.")

        # Проверка сохраненного решения в БД
        saved_resolution = get_setting(setting_key)
        if saved_resolution:
            # Убедимся, что сохраненный ID все еще валиден (мало ли)
            if any(c['id'] == saved_resolution for c in matches_coins):
                 print(f"Используется сохраненное разрешение из БД: '{search_string_lower}' -> '{saved_resolution}'")
                 return saved_resolution
            else:
                 print(f"Сохраненное разрешение '{saved_resolution}' для '{search_string_lower}' больше не актуально среди конфликтующих ID. Попытка нового разрешения.")
                 # В этом случае продолжаем, чтобы найти новое решение

        # Если нет в БД или старое неактуально - получаем market cap
        print(f"Попытка разрешения по market cap через API...")
        best_id = None
        max_market_cap = -1 # Начинаем с -1

        try:
            # Делаем ОДИН запрос для всех конфликтующих ID
            # ВАЖНО: get_coins_markets может вернуть данные не в том порядке, как ID в запросе
            market_data = cg.get_coins_markets(ids=conflicting_ids, vs_currency='usd') # Запрашиваем только USD, market_cap

            if not market_data:
                 print("Ошибка: API get_coins_markets вернул пустой результат.")
                 # Возвращаем первый как запасной вариант
                 best_id = conflicting_ids[0]
                 print(f"Не удалось получить данные о капитализации. Возвращаем первый ID: {best_id}")

            else:
                # Ищем монету с максимальной капитализацией
                for data in market_data:
                    current_id = data.get('id')
                    current_cap = data.get('market_cap')
                    # print(f"  Проверка: {current_id}, Капитализация: {current_cap}") # Отладка
                    # Обработка случая, когда market_cap = None
                    if current_id and current_cap is not None:
                        if current_cap > max_market_cap:
                            max_market_cap = current_cap
                            best_id = current_id

                if best_id:
                    print(f"Выбран ID '{best_id}' с максимальной капитализацией ({max_market_cap}).")
                    # Сохраняем результат в БД
                    if set_setting(setting_key, best_id):
                        print(f"Разрешение для '{search_string_lower}' -> '{best_id}' сохранено в БД.")
                    else:
                        print(f"Ошибка сохранения разрешения для '{search_string_lower}' в БД.")
                else:
                    # Если по какой-то причине не нашли ID с > -1 капитализацией
                    best_id = conflicting_ids[0]
                    print(f"Не удалось определить ID с максимальной капитализацией. Возвращаем первый ID: {best_id}")


        except (requests.exceptions.RequestException, ValueError, KeyError) as e:
            print(f"Ошибка API при получении market cap для разрешения конфликта '{search_string_lower}': {e}")
            if isinstance(e, ValueError) and '429' in str(e):
                print("Сработал Rate Limit API!")
                time.sleep(10) # Небольшая пауза
            # Возвращаем первый как запасной вариант при любой ошибке API
            best_id = conflicting_ids[0]
            print(f"Возвращаем первый ID из-за ошибки API: {best_id}")
        except Exception as e: # Ловим прочие неожиданные ошибки
             print(f"Неожиданная ошибка при разрешении конфликта для '{search_string_lower}': {e}")
             traceback.print_exc()
             best_id = conflicting_ids[0]
             print(f"Возвращаем первый ID из-за неожиданной ошибки: {best_id}")


        return best_id # Возвращаем результат (лучший или первый)

    else: # len(matches_coins) == 0
        # --- Шаг 4: Поиск по имени (если по символу не нашли) - опционально ---
        # ... (можно добавить, если нужно) ...
        # print(f"DEBUG: Не найдено ни по ID, ни по символу: {search_string_lower}") # Отладка
        return False # Ничего не найдено

    

# ИЗМЕНЕНО: get_sym_by_id использует кэшированный список
def get_sym_by_id(coin_id):
	global coins_list
	coin_id_lower = coin_id.lower()
	# Убедимся, что список монет есть (аналогично get_id_by_string)
	if not coins_list:
		saved_list_json = get_setting('coins_list')
		if saved_list_json:
			try:
				coins_list = json.loads(saved_list_json)
			except json.JSONDecodeError: return None # Не можем найти без списка
		else: return None # Не можем найти без списка

	for coin in coins_list:
		if coin.get('id', '').lower() == coin_id_lower:
			return coin.get('symbol', '').upper() # Возвращаем символ в верхнем регистре
	return None # Не найден

# ИЗМЕНЕНО: converter принимает разобранные аргументы
def converter(amount, cur1_str, cur2_str):
	# 1. Определяем ID для первой валюты
	cur1_id = get_id_by_string(cur1_str)
	if not cur1_id:
		# Проверяем, не является ли cur1_str известной фиатной валютой (USD, EUR, RUB...)
		# CoinGecko поддерживает их как vs_currencies
		vses = None
		try:
			vses = cg.get_supported_vs_currencies()
		except Exception as e:
			print(f"Ошибка получения supported_vs_currencies: {e}")

		if vses and cur1_str.lower() in vses:
			# Это фиатная валюта (исходная)
			cur2_id = get_id_by_string(cur2_str)
			if not cur2_id:
				return f"Не знаю вторую валюту: {cur2_str}"
			# Считаем cur2 к cur1 (обратный курс)
			try:
				req = cg.get_price(ids=cur2_id, vs_currencies=cur1_str.lower())
				if cur2_id in req and cur1_str.lower() in req[cur2_id]:
					rate = float(req[cur2_id][cur1_str.lower()])
					if rate == 0: return f"Не удалось получить курс {cur2_id} к {cur1_str.upper()} (курс равен 0)."
					result = amount / rate
					cur2_sym = get_sym_by_id(cur2_id) or cur2_id
					return f"{amount} {cur1_str.upper()} = {result:.8f} {cur2_sym} ({cur2_id})"
				else:
					return f"Не удалось получить курс {cur2_id} к {cur1_str.upper()}."
			except (requests.exceptions.RequestException, ValueError, KeyError) as e:
				print(f"Ошибка конвертации {cur2_id} к {cur1_str}: {e}")
				return f"Ошибка получения курса {cur2_id} к {cur1_str.upper()}."
		else:
			return f"Не знаю первую валюту: {cur1_str}"

	# Если cur1_id найден (это крипта)
	cur1_sym = get_sym_by_id(cur1_id) or cur1_id

	# 2. Проверяем вторую валюту (может быть криптой или фиатом)
	cur2_id = get_id_by_string(cur2_str)
	if cur2_id: # Вторая тоже крипта
		# Конвертация крипта -> крипта (через USD)
		try:
			# Получаем обе цены к USD
			rates = cg.get_price(ids=f'{cur1_id},{cur2_id}', vs_currencies='usd')
			price1_usd = rates.get(cur1_id, {}).get('usd')
			price2_usd = rates.get(cur2_id, {}).get('usd')

			if price1_usd is None or price2_usd is None:
				return f"Не удалось получить курсы к USD для {cur1_sym} или {get_sym_by_id(cur2_id) or cur2_id}."
			if price2_usd == 0:
				return f"Курс {get_sym_by_id(cur2_id) or cur2_id} к USD равен 0, конвертация невозможна."

			result = amount * (price1_usd / price2_usd)
			cur2_sym = get_sym_by_id(cur2_id) or cur2_id
			return f"{amount} {cur1_sym} ({cur1_id}) = {result:.8f} {cur2_sym} ({cur2_id})"

		except (requests.exceptions.RequestException, ValueError, KeyError) as e:
			print(f"Ошибка конвертации {cur1_id} к {cur2_id}: {e}")
			return f"Ошибка получения курсов для конвертации {cur1_sym} в {get_sym_by_id(cur2_id) or cur2_id}."

	else: # Вторая валюта не крипта, проверяем фиат
		vses = None
		try:
			vses = cg.get_supported_vs_currencies()
		except Exception as e:
			print(f"Ошибка получения supported_vs_currencies: {e}")

		if vses and cur2_str.lower() in vses:
			# Вторая валюта - фиат
			try:
				req = cg.get_price(ids=cur1_id, vs_currencies=cur2_str.lower())
				if cur1_id in req and cur2_str.lower() in req[cur1_id]:
					rate = float(req[cur1_id][cur2_str.lower()])
					result = amount * rate
					# Форматируем результат для фиата (2 знака после запятой)
					result_str = f"{result:.2f}" if cur2_str.lower() in ['usd', 'eur', 'gbp', 'rub'] else f"{result:.8f}"
					return f"{amount} {cur1_sym} ({cur1_id}) = {result_str} {cur2_str.upper()}"
				else:
					return f"Не удалось получить курс {cur1_sym} к {cur2_str.upper()}."
			except (requests.exceptions.RequestException, ValueError, KeyError) as e:
				print(f"Ошибка конвертации {cur1_id} к {cur2_str}: {e}")
				return f"Ошибка получения курса {cur1_sym} к {cur2_str.upper()}."
		else:
			return f"Не знаю вторую валюту: {cur2_str}"


# valutes = '' # Больше не используется

# Загружаем список монет при старте из БД или API
def initial_load_coins():
	global coins_list, last_coins_list_refresh_time
	print("Загрузка списка монет при старте...")
	saved_list_json = get_setting('coins_list')
	loaded_from_db = False
	if saved_list_json:
		try:
			coins_list = json.loads(saved_list_json)
			# Нужна проверка времени сохранения, но ее нет в БД.
			# Считаем, что если список есть, он достаточно свежий для старта.
			last_coins_list_refresh_time = datetime.now() - timedelta(minutes=coins_list_refresh_interval.total_seconds()/120) # Устанавливаем время, чтобы он обновился не сразу, но и не через час
			print(f"Список монет ({len(coins_list)}) загружен из БД.")
			loaded_from_db = True
		except json.JSONDecodeError:
			print("Ошибка декодирования списка монет из БД. Загрузка из API...")
			coins_list = [] # Очищаем на случай ошибки
			last_coins_list_refresh_time = None

	if not loaded_from_db:
		refresh_coins_list_if_needed(force_refresh=True)

initial_load_coins()


def filter_bullshit(coin_info):
	# Эту функцию можно доработать, она получает строку с Markdown
	# Нужно парсить ее или передавать сюда словарь q из get_info_from_id
	strikes = []
	# Пример простой проверки по тексту (может быть неточным)
	coin_info_lower = coin_info.lower()
	stop_words = ['meta','shiba','inu','meme','zilla','doge', 'verse', 'floki', 'baby', 'elon', 'potter', 'obama', 'sonic'] # Добавлены слова из логов

	# Пример поиска стоп-слов
	for word in stop_words:
		# Ищем слово целиком или как часть ID/имени
		if f' {word} ' in coin_info_lower or f'-{word}' in coin_info_lower or f'{word}-' in coin_info_lower or f'({word})' in coin_info_lower:
			if word not in strikes: strikes.append(f'word: {word}')

	# Пример поиска L2 (очень грубый)
	if "binance smart chain" in coin_info_lower or 'bscscan.com' in coin_info_lower: strikes.append('L2 BSC')
	if "polygon ecosystem" in coin_info_lower or 'polygonscan.com' in coin_info_lower: strikes.append('L2 Polygon')
	if "etherscan.io" in coin_info_lower or "ethplorer.io" in coin_info_lower: strikes.append('L2 ETH')
	if "solscan.io" in coin_info_lower or "explorer.solana.com" in coin_info_lower: strikes.append('L2 SOL')
	# ... добавить другие L2

	if "non-fungible tokens (nft)" in coin_info_lower:
		if 'NFT' not in strikes: strikes.append('NFT')

	# Проверка GitHub (упрощенная)
	if '*github:*' not in coin_info_lower and '*bitbucket:*' not in coin_info_lower:
	 	if 'closed source?' not in strikes: strikes.append('closed source?')
	# Более сложная проверка требует парсинга ссылок и запросов к GitHub, что вернет rate limit

	return strikes

# ИЗМЕНЕНО: recheck_list использует кэшированный список и реже вызывается
def recheck_list():
	global coins_list # Используем глобальный кэш
	# Эта функция теперь вызывается из getcourses_loop, который сам по себе реже работает
	# Дополнительно убедимся, что список свежий
	if not refresh_coins_list_if_needed():
		print("Проверка новых монет пропущена: не удалось обновить список.")
		return

	ocl_json = get_setting('old_coins_list') # Используем другое имя настройки для сравнения
	old_coins_map = {}
	if ocl_json:
		try:
			# Преобразуем старый список в словарь для быстрого поиска по ID
			old_coins_list_data = json.loads(ocl_json)
			if isinstance(old_coins_list_data, list):
				old_coins_map = {coin.get('id'): coin for coin in old_coins_list_data if coin.get('id')}
			else:
				print("Ошибка формата old_coins_list в БД.")
		except json.JSONDecodeError:
			print("Ошибка декодирования old_coins_list из БД.")

	# Сравниваем текущий кэш со старым списком из БД
	diff = []
	current_coins_map = {coin.get('id'): coin for coin in coins_list if coin.get('id')}

	for coin_id, coin_data in current_coins_map.items():
		if coin_id not in old_coins_map:
			diff.append(coin_data) # Нашли новую монету

	if diff:
		print(Fore.GREEN + f"{datetime.now().strftime('%H:%M:%S')} - Найдено {len(diff)} новых монет!" + Fore.WHITE)
		# print(diff) # Отладка

		# Получаем список чатов для уведомлений
		na_json = get_setting('newalerts')
		newalerts_chat_ids = []
		if na_json:
			try:
				newalerts_chat_ids = json.loads(na_json)
				if not isinstance(newalerts_chat_ids, list): newalerts_chat_ids = []
			except json.JSONDecodeError: pass

		if newalerts_chat_ids:
			print(f"Отправка уведомлений о новых монетах в чаты: {newalerts_chat_ids}")
			base_message = f"*Новые монеты ({len(diff)}):*\n"
			full_diff_str = ""
			coins_processed = 0

			for coin_data in diff:
				coin_id = coin_data.get('id')
				if not coin_id: continue

				nfo = get_info_from_id(coin_id) # Получаем инфо (использует кэш деталей)
				fb = filter_bullshit(nfo) # Фильтруем

				coin_block = ""
				if len(fb) >= 2 and 'closed source?' in fb: # Пример более строгой фильтрации булшита
					coin_block += f"{nfo.splitlines()[0]}\n" # Только первая строка (ID/SYM/NAME)
					coin_block += "*Предположительно буллшит:* " + ', '.join(fb) + "\n"
				else:
					coin_block += nfo # Полная инфа
					if fb:
						coin_block += "\n*Возможные признаки буллшита:* " + ', '.join(fb) + "\n"

				coin_block += "====================\n"

				# Проверяем длину сообщения перед добавлением
				if len(base_message) + len(full_diff_str) + len(coin_block) > 4000: # Оставляем запас
					# Отправляем накопленное сообщение
					for chat_id in newalerts_chat_ids:
						say({'chat': {'id': chat_id}}, base_message + full_diff_str, silent=True)
					# Начинаем новое сообщение
					full_diff_str = coin_block
					time.sleep(0.5) # Пауза между сообщениями
				else:
					full_diff_str += coin_block

				coins_processed += 1
				if coins_processed % 5 == 0: time.sleep(0.2) # Небольшая пауза каждые 5 монет


			# Отправляем остаток, если есть
			if full_diff_str:
				for chat_id in newalerts_chat_ids:
					say({'chat': {'id': chat_id}}, base_message + full_diff_str if len(diff) > coins_processed else full_diff_str, silent=True)


		# Обновляем 'old_coins_list' в БД текущим списком
		set_setting('old_coins_list', json.dumps(coins_list))
		print("Список old_coins_list в БД обновлен.")
	# else: print ('Нет новых монет.') # Отладка

run = True
# Запускаем основной цикл получения курсов в отдельном потоке
getcourses_loop() # Первый запуск сразу, остальные по таймеру внутри

# Основной цикл программы
try:
	print("Бот запущен. Ожидание сообщений...")
	# bot.message_loop() # message_loop блокирующий, используем polling или webhook в проде
	# Для простоты оставим message_loop, но помним о блокировке

	# Запускаем message_loop так, чтобы он проверял 'run'
	# Передаем 'run_forever=False' и управляем циклом сами
	# Или используем подход с потоком для message_loop, если он блокирует
	# Вариант 1: Простой цикл с message_loop (может блокировать другие вещи, если логика сложная)
	# bot.message_loop(run_forever=run) # run_forever читается один раз при запуске, не подходит

	# Вариант 2: Цикл while с небольшой задержкой (если message_loop не блокирует)
	# bot.message_loop() # Запускаем обработку сообщений (она должна быть неблокирующей или в потоке)
	# Этот цикл будет ждать, пока run не станет False (из-за /reload или KeyboardInterrupt)
	# while run:
	#	 time.sleep(1)

	# Вариант 3 (Рекомендуемый, если telepot.message_loop блокирующий):
	# Запускаем message_loop в отдельном потоке
	from telepot.loop import MessageLoop
	message_loop_thread = threading.Thread(target=MessageLoop(bot).run_forever, daemon=True)
	message_loop_thread.start()
	print("Message loop запущен в отдельном потоке.")

	# Основной поток теперь просто ждет сигнала остановки
	while run:
		time.sleep(1)

	print("Завершение работы основного цикла...")
	# Дополнительные действия по очистке, если нужны

except KeyboardInterrupt:
	print("\nПолучено прерывание клавиатуры (Ctrl+C). Завершение работы...")
	run = False # Сигнал для остановки таймеров
	stopthreads() # Отменяем таймеры
	# Поток MessageLoop завершится сам, т.к. он daemon

except Exception as e:
	print("\nНепредвиденная ошибка в основном цикле:")
	print(traceback.format_exc())
	run = False
	stopthreads()

finally:
	# Даем немного времени на завершение потоков перед закрытием БД
	time.sleep(2)
	print("Закрытие соединения с БД...")
	try:
		db.commit() # Финальный коммит на всякий случай
		db.close()
	except Exception as db_err:
		print(f"Ошибка при закрытии БД: {db_err}")

	print("Выход.")
	# Если был /reload, запускаем скрипт перезапуска
	# Используем try-except на случай, если usercheck не определен
	try:
		if not run and usercheck == "Brakhma": # Проверяем, была ли команда reload
			print("Выполнение reload.sh...")
			try:
				# Запускаем в фоне, чтобы этот скрипт мог завершиться
				os.system("./reload.sh &")
			except Exception as reload_err:
				print(f"Ошибка при запуске reload.sh: {reload_err}")
	except NameError:
		pass # usercheck не был определен, значит /reload не вызывался
