#!/usr/bin/env python3
# coding: utf-8
import re
import traceback
import sys
import time
import random
import telepot
import os
from os.path import isfile
from datetime import datetime, timedelta # Нужны для Python 3.5
from colorama import init, Fore
import json
import urllib.request
import urllib.error # Добавлено для обработки ошибок сети
import sqlite3
import threading
from pycoingecko import CoinGeckoAPI
import requests # для отлова ошибок HTTP
from telepot.loop import MessageLoop # Для неблокирующего message_loop

# --- Импорт зависимостей OKEx и настроек ---
try:
	from okex import okex # Убедись, что этот модуль установлен и настроен
except ImportError:
	print("Предупреждение: Модуль 'okex' не найден. Функционал /fund и /add_shares будет недоступен.")
	okex = None # Определяем как None, чтобы избежать NameError

try:
	from settings import TOKEN, okex_apikey, okex_secret, okex_passphrase # Импортируем токен и ключи
except ImportError:
	print("Ошибка: Не найден файл settings.py или в нем отсутствуют TOKEN/ключи OKEx.")
	TOKEN = input('Введите Bot-API токен: ')
	# Ключи OKEx тоже нужно будет как-то получить или задать
	okex_apikey = None
	okex_secret = None
	okex_passphrase = None
# --- Конец импорта зависимостей OKEx ---

cg = CoinGeckoAPI()

init() #colorama init
os.chdir(os.path.dirname(os.path.abspath(__file__)))

upd_interval = 300 # Интервал обновления курсов (5 минут)
coins_list_refresh_interval = timedelta(hours=1) # Интервал обновления списка монет (1 час)

# Глобальные переменные для кэширования
coins_list = [] # кэш списка монет
last_coins_list_refresh_time = None # время последнего обновления списка
coin_details_cache = {} # кэш для деталей монет (чтобы не дергать get_coin_by_id часто)
coin_details_cache_ttl = timedelta(hours=6) # время жизни кэша деталей

#Загрузка словарей=============================
reg_answers = []
def load_dic(dic):
	global reg_answers
	try:
		file_path = os.path.join('DICT', dic) # Используем os.path.join
		file = open(file_path, 'r', encoding="utf8")
		# dict_entry = {'reg': None, 'answers': []} # Не используется в этой логике
		current_answers = []
		current_reg = None
		count = 0 # Счетчик регулярок

		for line in file:
			line = line.strip()
			if line.startswith('#') or not line:
				if current_reg and current_answers:
					reg_answers.append({'reg': current_reg, 'answers': current_answers})
					count +=1
					current_answers = []
					current_reg = None
				continue

			if line.startswith('^'):
				if current_reg and current_answers:
					reg_answers.append({'reg': current_reg, 'answers': current_answers})
					count +=1

				try:
					# Убираем последний символ ('^'), если он там есть по ошибке
					reg_pattern = line[1:] if line.endswith('^') and len(line) > 1 else line
					current_reg = re.compile(reg_pattern.lower()) # Компилируем без '^' в начале Python строки
					current_answers = []
				except re.error as e:
					# Замена f-string на .format()
					print("Ошибка компиляции регулярного выражения в {}: {} - {}".format(dic, line, e))
					current_reg = None
			elif current_reg:
				current_answers.append(line)

		if current_reg and current_answers:
			reg_answers.append({'reg': current_reg, 'answers': current_answers})
			count += 1

		file.close()
		# Замена f-string на .format()
		print('Loaded: {} ({} re)'.format(dic, count))
		return count
	except FileNotFoundError:
		# Замена f-string на .format()
		print("Ошибка: Файл словаря не найден - {}".format(file_path))
		return 0
	except Exception as e:
		# Замена f-string на .format()
		print("Ошибка при загрузке словаря {}: {}".format(dic, e))
		traceback.print_exc()
		return 0


def loadreg():
	global reg_answers
	reg_answers = []
	dict_dir = 'DICT'
	if not os.path.exists(dict_dir):
		print("Папка DICT не найдена.")
		return
	dicts = os.listdir(dict_dir)
	total_re = 0
	for file in dicts:
		if file.endswith('.dic'):
			loaded_count = load_dic(file)
			if loaded_count:
				total_re += loaded_count # Суммируем успешно загруженные
		else:
			print(file + ' не загружен (не .dic)')
	print("Всего загружено регулярок: {}".format(total_re))

loadreg()

#БАЗА=====================================
db = sqlite3.connect('moisha.db', check_same_thread=False)
cur = db.cursor()
try:
	db.execute('''CREATE TABLE IF NOT EXISTS chat_alerts(
                  id INTEGER PRIMARY KEY,
                  alerts TEXT
                )''')
	db.execute('''CREATE TABLE IF NOT EXISTS settings(
                  setting TEXT PRIMARY KEY,
                  value TEXT
                )''')
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
		# Оставляем .format для имени таблицы, но это требует доверия к переменной 'table'
		query = "SELECT * FROM {}".format(table)
		params = []
		if cond:
			# ВАЖНО: Для безопасности cond должен использовать '?' и параметры нужно передавать в execute
			# Текущая реализация просто вставляет строку cond, что НЕБЕЗОПАСНО, если cond приходит извне.
			# Пример безопасного вызова (требует переделки): cur.execute("SELECT * FROM chat_alerts WHERE id = ?", (chat_id,))
			query += " WHERE {}".format(cond) # Оставляем для совместимости, но это небезопасно

		# print("DEBUG SQL:", query) # Отладка SQL
		cur.execute(query) # Если бы были параметры: cur.execute(query, params)
		data = cur.fetchall()
		return data
	except sqlite3.Error as err:
		# Замена f-string на .format()
		print("Ошибка SQLite при получении данных из {}: {}".format(table, err))
		print(traceback.format_exc())
		return None

def get_alerts(chat_id):
	# Формируем безопасное условие для get_data (пример)
	# cond = "id = ?" # Условие с плейсхолдером
	# params = (chat_id,) # Параметры в кортеже
	# result = get_data('chat_alerts', cond, params) # Нужна переделка get_data

	# Пока используем старый get_data, но с явным преобразованием id
	cond_str = "id = {}".format(int(chat_id)) # Преобразуем в int для базовой защиты
	result = get_data('chat_alerts', cond_str)

	if result:
		try:
			# Проверяем, что результат не пустой и содержит 'alerts'
			if result[0] and 'alerts' in result[0]:
				alerts_json = result[0]['alerts']
				if alerts_json: # Доп. проверка на пустую строку
					return json.loads(alerts_json)
				else:
					return [] # Пустая строка или None в БД
			else:
				# Запись есть, но она некорректна
				print("Некорректная запись алертов для чата {}".format(chat_id))
				return []
		except (json.JSONDecodeError, IndexError) as e:
			# Замена f-string на .format()
			print("Ошибка декодирования JSON или IndexError для чата {}: {}".format(chat_id, e))
			try:
				cur = db.cursor()
				cur.execute('''UPDATE chat_alerts SET alerts = ? WHERE id = ?''', (json.dumps([]), chat_id))
				db.commit()
				# Замена f-string на .format()
				print("Исправлена запись алертов для чата {}".format(chat_id))
			except sqlite3.Error as db_err:
				# Замена f-string на .format()
				print("Не удалось исправить запись алертов для чата {}: {}".format(chat_id, db_err))
			return []
	else:
		# Если записи нет, создаем ее
		try:
			cur = db.cursor()
			cur.execute('''INSERT INTO chat_alerts (id, alerts) VALUES (?, ?)''', (chat_id, json.dumps([])))
			db.commit()
			# Замена f-string на .format()
			print("Создана запись алертов для нового чата {}".format(chat_id))
			return []
		except sqlite3.Error as db_err:
			# Замена f-string на .format()
			print("Не удалось создать запись алертов для чата {}: {}".format(chat_id, db_err))
			return []

def set_alert(msg, coin_id, porog=1, current_price=None):
	global db
	chat_id = msg['chat']['id']

	if current_price is None:
		current_price = kurs(coin_id)
		if current_price is False:
			# Замена f-string на .format()
			say(msg, 'Не удалось получить текущий курс для {}. Алерт не добавлен.'.format(coin_id))
			return False

	cur = db.cursor()
	alerts = get_alerts(chat_id)
	new_list = []
	done = False

	if alerts:
		for alert in alerts:
			if alert['valute'] == coin_id:
				alert['time'] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
				alert['price'] = current_price
				alert['porog'] = str(porog)
				done = True
			new_list.append(alert)

	if not done:
		alert = {
			'time': datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
			'valute': coin_id,
			'price': current_price,
			'porog': str(porog)
		}
		new_list.append(alert)

	try:
		cur.execute('''UPDATE chat_alerts SET alerts = ? WHERE id = ?''', (json.dumps(new_list), chat_id))
		db.commit()
		if 'text' in msg:
			coin_sym = get_sym_by_id(coin_id) or coin_id
			# Замена f-string на .format()
			say(msg, 'Добавлен/обновлен алерт {} ({}) с порогом {}%'.format(coin_sym, coin_id, porog))
		return True
	except sqlite3.Error as e:
		# Замена f-string на .format()
		print("Ошибка SQLite при обновлении алертов для чата {}: {}".format(chat_id, e))
		if 'text' in msg:
			say(msg, 'Произошла ошибка при сохранении алерта.')
		return False


def remove_alert(msg, valute_str):
	global db
	chat_id = msg['chat']['id']
	coin_id = get_id_by_string(valute_str)

	if not coin_id:
		# Замена f-string на .format()
		say(msg, 'Не знаю такой валюты: {}'.format(valute_str))
		return

	cur = db.cursor()
	alerts = get_alerts(chat_id)
	new_list = []
	removed = False

	if alerts:
		for alert in alerts:
			if alert['valute'] == coin_id:
				removed = True
			else:
				new_list.append(alert)

		if removed:
			try:
				cur.execute('''UPDATE chat_alerts SET alerts = ? WHERE id = ?''', (json.dumps(new_list), chat_id))
				db.commit()
				coin_sym = get_sym_by_id(coin_id) or coin_id
				# Замена f-string на .format()
				say(msg, 'Алерт {} ({}) удалён.'.format(coin_sym, coin_id))
			except sqlite3.Error as e:
				# Замена f-string на .format()
				print("Ошибка SQLite при удалении алерта для чата {}: {}".format(chat_id, e))
				say(msg, 'Произошла ошибка при удалении алерта.')
		else:
			coin_sym = get_sym_by_id(coin_id) or coin_id
			# Замена f-string на .format()
			say(msg, 'Алерт {} ({}) не найден.'.format(coin_sym, coin_id))
	else:
		say(msg, 'Алерты для этого чата не настроены.')


def get_setting(setting):
	# Формируем безопасное условие для get_data
	# cond = "setting = ?"
	# params = (setting,)
	# result = get_data('settings', cond, params) # Нужна переделка get_data

	# Пока используем старый get_data, но экранируем кавычки в setting для базовой защиты
	# Это все еще НЕ идеально безопасно, если 'setting' может содержать вредный SQL
	escaped_setting = setting.replace("'", "''")
	cond_str = "setting = '{}'".format(escaped_setting)
	result = get_data('settings', cond_str)

	if not result:
		return None
	else:
		# Доп. проверка на случай, если вернулось несколько строк (хотя setting PRIMARY KEY)
		if isinstance(result, list) and len(result) > 0:
			return result[0].get('value') # Используем .get для безопасности
		else:
			return None # Неожиданный результат


def set_setting(setting, value):
	global db
	try:
		cur = db.cursor()
		cur.execute('''INSERT OR REPLACE INTO settings (setting, value) VALUES (?, ?)''', (setting, value))
		db.commit()
		return True
	except sqlite3.Error as err:
		# Замена f-string на .format()
		print("Ошибка SQLite при установке настройки '{}': {}".format(setting, err))
		print(traceback.format_exc())
		return False

#как называть пользователя в консоли и логах
def user_name(msg):
	try:
		# Проверяем наличие ключа 'username'
		if 'username' in msg.get('from', {}):
			name = '@' + msg['from']['username']
		else:
			# Собираем имя из first_name и last_name, если они есть
			first_name = msg.get('from', {}).get('first_name')
			last_name = msg.get('from', {}).get('last_name')
			if first_name and last_name:
				name = u'{} {}'.format(first_name, last_name) # Используем u'' для Python 2/3 совместимости имен
			elif first_name:
				name = first_name
			else: # Если имени нет, используем ID чата
				name = "cid:{}".format(msg['chat']['id'])
	except Exception: # Общий обработчик на всякий случай
		# Замена f-string на .format()
		name = "cid:{}".format(msg.get('chat', {}).get('id', 'UNKNOWN'))
	return name


#собсна бот
class YourBot(telepot.Bot):
	def __init__(self, *args, **kwargs):
		super(YourBot, self).__init__(*args, **kwargs)
		self._answerer = telepot.helper.Answerer(self)
		self._message_with_inline_keyboard = None

	def on_chat_message(self, msg):
		content_type, chat_type, chat_id = telepot.glance(msg)

		if content_type == 'new_chat_member':
			try:
				if msg['new_chat_participant']['id'] != self.getMe()['id']:
					# Используем .get() для chat_id на всякий случай
					bot.sendSticker(msg.get('chat', {}).get('id'), random.choice(['CAADAgADnAEAAr8cUgGqoY57iHWJagI','CAADAgADWAEAAr8cUgHoHDucQspSKwI']))
			except Exception as e:
				# Замена f-string на .format()
				print("Ошибка при отправке стикера new_chat_member: {}".format(e))
			return

		if content_type != 'text':
			# Замена f-string на .format()
			print('{} {} {} {}'.format(datetime.now().strftime("%d.%m.%Y %H:%M:%S"), content_type, chat_type, chat_id))
			return

		# Обработка текстовых сообщений
		user = user_name(msg)
		text = msg.get('text', '') # Безопасно получаем текст
		try:
			# Замена f-string на .format()
			print(Fore.RED + datetime.now().strftime("%d.%m.%Y %H:%M:%S") + ' {}: '.format(user) + Fore.WHITE + text)
		except UnicodeEncodeError:
			# Замена f-string на .format()
			print('{0} {1}: [UnicodeEncodeError]'.format(datetime.now().strftime("%d.%m.%Y %H:%M:%S"), user))
		except Exception as e:
			# Замена f-string на .format()
			print("Ошибка вывода сообщения в консоль: {}".format(e))

		# Вызываем обработчик, только если есть текст
		if text:
			process(msg)

		# Логирование после обработки
		try:
			with open('tg.log', 'a', encoding="utf8") as log:
				# Замена f-string на .format()
				log.write("{0} - {1}\n".format(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg))
		except Exception as e:
			# Замена f-string на .format()
			print("Ошибка записи в tg.log: {}".format(e))


	def on_edited_chat_message(self, msg):
		pass

def say(msg, answer, silent=False):
	chat_id = msg['chat']['id']
	user = user_name(msg) # Получаем имя пользователя один раз

	try:
		answer = answer.replace('[name]', user)
		answer = answer.replace('[br]', '\n')

		if '[courses]' in answer:
			try:
				alerts = get_alerts(chat_id)
				stringg = ''
				alert_ids = [a['valute'] for a in alerts if a.get('valute')] # Собираем ID с проверкой
				if alert_ids:
					prices = get_prices_for_ids(alert_ids)
					if prices:
						for alert in alerts:
							coin_id = alert.get('valute')
							if not coin_id: continue # Пропускаем алерт без ID

							coin_sym = get_sym_by_id(coin_id) or coin_id
							price_data = prices.get(coin_id, {})
							price = price_data.get('usd', 'N/A') if isinstance(price_data, dict) else 'N/A' # Проверка типа

							if price != 'N/A':
								try:
									# Форматируем цену, избегая научной нотации для маленьких чисел
									price_str = "{:.8f}".format(float(price)).rstrip('0').rstrip('.')
									# Замена f-string на .format()
									stringg += '*{} ({})*: {}$\n'.format(coin_sym, coin_id, price_str)
								except (ValueError, TypeError):
									# Замена f-string на .format()
									stringg += '*{} ({})*: Ошибка формата цены ({})\n'.format(coin_sym, coin_id, price)
							else:
								# Замена f-string на .format()
								stringg += '*{} ({})*: Ошибка цены\n'.format(coin_sym, coin_id)
					else:
						stringg = 'Не удалось загрузить курсы.\n'
				else:
					stringg = 'Настройте алерты: /alert <валюта> [порог%]\n'

				answer = answer.replace('[courses]', stringg.strip())
			except Exception as e:
				# Замена f-string на .format()
				print("Ошибка при обработке [courses] для чата {}: {}".format(chat_id, e))
				traceback.print_exc()
				answer = answer.replace('[courses]', '*Ошибка при получении курсов*')

		# Отправка длинных сообщений
		if len(answer) > 4096:
			parts = []
			text_part = answer # Переименовано во избежание конфликта с аргументом msg['text']
			while len(text_part) > 0:
				if len(text_part) > 4096:
					part = text_part[:4096]
					first_lnbr = part.rfind('\n')
					if first_lnbr != -1:
						parts.append(part[:first_lnbr])
						text_part = text_part[first_lnbr:].lstrip()
					else:
						parts.append(part)
						text_part = text_part[4096:]
				else:
					parts.append(text_part)
					break
			for part in parts:
				bot.sendMessage(chat_id, part, parse_mode='Markdown', disable_web_page_preview=True)
				time.sleep(0.1)
		else:
			bot.sendMessage(chat_id, answer, parse_mode='Markdown', disable_web_page_preview=True)

		if not silent:
			try:
				log_answer = re.sub(r'[*_`\[\]()]', '', answer) # Убираем Markdown
				# Замена f-string на .format()
				print(Fore.GREEN + datetime.now().strftime("%d.%m.%Y %H:%M:%S") + ' Мойша (to {}): '.format(user) + Fore.WHITE + log_answer)
			except UnicodeEncodeError:
				# Замена f-string на .format()
				print('{0} Мойша (to {1}): [UnicodeEncodeError]'.format(datetime.now().strftime("%d.%m.%Y %H:%M:%S"), user))
			except Exception as e:
				# Замена f-string на .format()
				print("Ошибка вывода ответа Мойши в консоль: {}".format(e))

	except telepot.exception.TelegramError as e:
		# Замена f-string на .format()
		print("Ошибка Telegram API при отправке сообщения в чат {}: {}".format(chat_id, e))
	except Exception as e:
		# Замена f-string на .format()
		print("Неизвестная ошибка в функции say для чата {}: {}".format(chat_id, e))
		traceback.print_exc()

def get_prices_for_ids(ids_list):
	if not ids_list:
		return {}
	unique_ids = list(set(ids_list))
	ids_string = ','.join(unique_ids)
	try:
		# print("Запрос цен для: {}".format(ids_string)) # Отладка
		data = cg.get_price(ids=ids_string, vs_currencies='usd')
		# print("Ответ цен: {}".format(data)) # Отладка
		return data
	except (requests.exceptions.RequestException, ValueError, KeyError) as e:
		if isinstance(e, ValueError) and '429' in str(e):
			# Замена f-string на .format()
			print("{} - Rate limit при получении цен!".format(datetime.now().strftime('%H:%M:%S')))
			time.sleep(10)
		else:
			# Замена f-string на .format()
			print("Ошибка при получении цен для {} ID: {}".format(len(unique_ids), e))
		return None

# Основной цикл получения курсов и проверки алертов
def getcourses_loop():
	try:
		# Замена f-string на .format()
		print("{} - Начало цикла getcourses".format(datetime.now().strftime('%H:%M:%S')))
		all_chat_alerts = get_data('chat_alerts')
		if all_chat_alerts is None:
			print("Ошибка получения данных алертов из БД. Пропуск цикла.")
			# Не перезапускаем таймер здесь, а в finally
			return

		unique_valute_ids = set()
		chat_alerts_map = {}

		for chat_data in all_chat_alerts:
			chat_id = chat_data.get('id')
			alerts_json = chat_data.get('alerts')
			if not chat_id or alerts_json is None: continue # Пропускаем некорректные записи

			try:
				alerts = json.loads(alerts_json)
				if isinstance(alerts, list): # Убедимся, что это список
					chat_alerts_map[chat_id] = alerts
					for alert in alerts:
						valute = alert.get('valute') # Безопасное получение ID
						if valute: unique_valute_ids.add(valute)
				else:
					# Замена f-string на .format()
					print("Алерты для чата {} не являются списком. Сброс.".format(chat_id))
					chat_alerts_map[chat_id] = [] # Сбрасываем в пустой список
			except json.JSONDecodeError:
				# Замена f-string на .format()
				print("Ошибка декодирования JSON алертов для чата {}. Пропуск чата.".format(chat_id))
				continue

		if not unique_valute_ids:
			print("Нет настроенных алертов. Пропуск запроса цен.")
			# Не перезапускаем таймер здесь, а в finally
			return

		current_prices = get_prices_for_ids(list(unique_valute_ids))

		if current_prices is None:
			print("Не удалось получить цены. Пропуск обработки алертов.")
			# Не перезапускаем таймер здесь, а в finally
			return

		do_chat_alerts(chat_alerts_map, current_prices)

		# Проверяем новые монеты (вызываем реже)
		refresh_coins_list_if_needed() # Обновляем, если пора
		recheck_list() # Сравниваем с сохраненным старым списком

		# Замена f-string на .format()
		print("{} - Конец цикла getcourses".format(datetime.now().strftime('%H:%M:%S')))

	except Exception as err:
		# Замена f-string на .format()
		print("Критическая ошибка в цикле getcourses_loop: {}".format(err))
		print(traceback.format_exc())
	finally:
		# Перезапускаем таймер только если бот все еще должен работать (run=True)
		# Переменная run определяется глобально позже
		if run:
			getcourses_timer = threading.Timer(upd_interval, getcourses_loop)
			getcourses_timer.name = 'getcourses_timer'
			getcourses_timer.start()
		else:
			print("Бот остановлен, таймер getcourses_loop не перезапущен.")


def valid_valute(valute_str):
	return bool(get_id_by_string(valute_str))

def kurs(coin_id):
	if not coin_id:
		return False
	try:
		# print("Запрос kurs для: {}".format(coin_id)) # Отладка
		result = cg.get_price(ids=coin_id, vs_currencies='usd')
		# print("Ответ kurs: {}".format(result)) # Отладка
		if coin_id in result and 'usd' in result[coin_id]:
			# Доп. проверка, что значение не None
			price = result[coin_id]['usd']
			return float(price) if price is not None else False
		else:
			# Замена f-string на .format()
			print("Неожиданный формат ответа от get_price для {}: {}".format(coin_id, result))
			return False
	except (requests.exceptions.RequestException, ValueError, KeyError) as e:
		if isinstance(e, ValueError) and '429' in str(e):
			# Замена f-string на .format()
			print("{} - Rate limit при запросе kurs для {}!".format(datetime.now().strftime('%H:%M:%S'), coin_id))
			time.sleep(10)
		else:
			# Замена f-string на .format()
			print("Ошибка при получении курса для {}: {}".format(coin_id, e))
		return False

def tonmine (power_consumption = 0.1, power_price = 5):
	try:
		# Добавляем User-Agent, чтобы избежать блокировки
		headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'}
		request = urllib.request.Request('https://ton-reports-24d2v.ondigitalocean.app/report/pool-profitability', headers=headers)
		response = urllib.request.urlopen(request, timeout=10)
		json_received = (response.read()).decode('utf-8', errors='ignore')
		p = json.loads(json_received)
		profitability = int(p['profitabilityPerGh'])/(10**9)
		net_cost = (power_consumption*24*power_price)/profitability if profitability else 0
		# Замена f-string на .format() с форматированием чисел
		return 'avg prof: {:.8f} TON/Gh/day\nСебестоимость: {:.2f} руб/TON'.format(profitability, net_cost)
	except urllib.error.URLError as e:
		# Замена f-string на .format()
		print("Ошибка сети при запросе Ton profitability: {}".format(e))
		return "Ошибка получения данных о доходности майнинга (сеть)."
	except (json.JSONDecodeError, KeyError, ValueError) as e:
		# Замена f-string на .format()
		print("Ошибка обработки данных Ton profitability: {}".format(e))
		return "Ошибка получения данных о доходности майнинга (формат)."
	except Exception as e:
		# Замена f-string на .format()
		print("Неизвестная ошибка в tonmine: {}".format(e))
		traceback.print_exc()
		return "Неизвестная ошибка при расчете доходности майнинга."


def get_coin_details_cached(coin_id):
	global coin_details_cache
	now = datetime.now()
	if coin_id in coin_details_cache:
		data, timestamp = coin_details_cache[coin_id]
		if now - timestamp < coin_details_cache_ttl:
			# print("Взят из кэша: {}".format(coin_id)) # Отладка
			return data

	try:
		# print("Запрос деталей для: {}".format(coin_id)) # Отладка
		q = cg.get_coin_by_id(coin_id, localization='false', tickers='false', market_data='true', community_data='false', developer_data='false', sparkline='false')
		coin_details_cache[coin_id] = (q, now)
		return q
	except (requests.exceptions.RequestException, ValueError) as e:
		if isinstance(e, ValueError) and '429' in str(e):
			# Замена f-string на .format()
			print("{} - Rate limit при запросе деталей {}!".format(datetime.now().strftime('%H:%M:%S'), coin_id))
			time.sleep(10)
		elif isinstance(e, ValueError) and 'invalid coin_id' in str(e).lower():
			# Замена f-string на .format()
			print("CoinGecko не нашел ID: {}".format(coin_id))
			return {'error': "ID {} не найден в CoinGecko.".format(coin_id)}
		else:
			# Замена f-string на .format()
			print("Ошибка при получении деталей для {}: {}".format(coin_id, e))
		if coin_id in coin_details_cache:
			del coin_details_cache[coin_id]
		return None

def get_info_from_id(coin_id):
	q = get_coin_details_cached(coin_id)

	if q is None:
		# Замена f-string на .format()
		return "Ошибка получения данных для ID: {}. Возможно, проблемы с API CoinGecko или rate limit.".format(coin_id)
	if 'error' in q:
		return q['error']
	if not isinstance(q, dict):
		# Замена f-string на .format()
		print("Неожиданный тип данных от get_coin_details_cached для {}: {}".format(coin_id, type(q)))
		# Замена f-string на .format()
		return "Неожиданный формат данных для ID: {}".format(coin_id)

	# Собираем строку с помощью .format()
	answ = "*ID:* {}\n*SYM:* {}\n*Name:* {}\n".format(
		q.get('id', 'N/A'),
		q.get('symbol', 'N/A').upper(),
		q.get('name', 'N/A')
	)

	price_rub = q.get('market_data', {}).get('current_price', {}).get('rub')
	price_usd = q.get('market_data', {}).get('current_price', {}).get('usd')
	if price_rub:
		answ += "*Цена:* ~{:.2f} руб.\n".format(price_rub)
	elif price_usd:
		answ += "*Price:* ~{:.4f} $.\n".format(price_usd)
	else:
		answ += "*Нет данных по цене.*\n"

	categories = q.get('categories')
	if categories:
		valid_cats = [cat for cat in categories if cat]
		if valid_cats:
			answ += "*categories:* " + ', '.join(valid_cats) + '\n'

	links_data = q.get('links', {})
	if links_data:
		links_str = ""
		homepage = [h for h in links_data.get('homepage', []) if h]
		if homepage: links_str += "*Homepage:* " + ', '.join(homepage) + "\n"
		explorers = [e for e in links_data.get('blockchain_site', []) if e]
		if explorers: links_str += "*Explorers:* " + ', '.join(explorers) + "\n"
		repos = links_data.get('repos_url', {})
		github_repos = [g for g in repos.get('github', []) if g]
		bitbucket_repos = [b for b in repos.get('bitbucket', []) if b]
		if github_repos: links_str += "*GitHub:* " + '\n'.join(github_repos) + "\n"
		if bitbucket_repos: links_str += "*Bitbucket:* " + '\n'.join(bitbucket_repos) + "\n"
		twitter = links_data.get('twitter_screen_name')
		# Замена f-string на .format() для Twitter
		if twitter: links_str += "*Twitter:* [@{}](https://twitter.com/{})\n".format(twitter, twitter)
		telegram = links_data.get('telegram_channel_identifier')
		# Замена f-string на .format() для Telegram
		if telegram: links_str += "*Telegram:* @{}\n".format(telegram)
		reddit = links_data.get('subreddit_url')
		# Замена f-string на .format() для Reddit
		if reddit: links_str += "*Reddit:* {}\n".format(reddit)

		if links_str:
			answ += "*Links:*\n" + links_str.replace('_', r'\_')

	return answ

# Функция process остается большой, заменяем f-строки внутри нее
def process (msg):
	global reg_answers, db, run, coins_list
	answer = ''
	if 'text' not in msg:
		return

	bot_username = bot.getMe().get('username')
	text = msg['text'] # Оригинальный текст
	if bot_username:
		text = text.replace('@' + bot_username, '').strip()
	else:
		print("Не удалось получить username бота")

	if not text:
		return

	# Используем text для команд
	command_parts = text.lower().split()
	command = command_parts[0]
	args = command_parts[1:]

	# --- Обработка команд с использованием .format() ---
	if command == '/alert' or command == '/alerts':
		chat_id = msg['chat']['id'] # Получаем ID чата
		if not args:
			if command == '/alerts':
				alerts = get_alerts(chat_id)
				stringg = ''
				if alerts:
					stringg += '*Текущие алерты:*\n'
					for alert in alerts:
						coin_id = alert.get('valute', 'N/A')
						coin_sym = get_sym_by_id(coin_id) or coin_id
						porog = alert.get('porog', '?')
						# Замена f-string на .format()
						stringg += '*{} ({})* - {}%\n'.format(coin_sym, coin_id, porog)
				else:
					stringg = 'Алерты не настроены. Используйте:\n`/alert <валюта> [порог%]`'
				say(msg, stringg.strip())
			else:
				say (msg, "Использование:\n`/alert <валюта> [порог%]`\nНапример:\n`/alert bitcoin 5` (оповещение при изменении на 5%)\n`/alert eth` (оповещение при изменении на 1%)")
			return

		valute_str = args[0]
		porog = 1
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
			# Замена f-string на .format()
			say(msg, 'Не знаю такой валюты: "{}". Попробуйте `/search {}`.'.format(valute_str, valute_str))
			return

		set_alert(msg, coin_id, porog)
		return

	elif command == '/noalert' or command == '/noalerts':
		chat_id = msg['chat']['id']
		if command == '/noalerts':
			try:
				cur = db.cursor()
				cur.execute('''UPDATE chat_alerts SET alerts = ? WHERE id = ?''', (json.dumps([]), chat_id))
				db.commit()
				say(msg, 'Все алерты удалены.')
			except sqlite3.Error as e:
				# Замена f-string на .format()
				print("Ошибка SQLite при удалении всех алертов для чата {}: {}".format(chat_id, e))
				say(msg, 'Произошла ошибка при удалении алертов.')
		elif not args:
			say(msg, 'Использование: `/noalert <валюта>`')
		else:
			valute_str = args[0]
			remove_alert(msg, valute_str)
		return

	elif command == '/newalerts':
		chat_id = msg['chat']['id']
		na_json = get_setting('newalerts')
		newalerts_list = []
		if na_json:
			try:
				newalerts_list = json.loads(na_json)
				if not isinstance(newalerts_list, list):
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
		refresh_coins_list_if_needed()
		if not coins_list:
			say(msg, "Не удалось загрузить список монет для поиска. Попробуйте позже.")
			return

		found = []
		for coin in coins_list:
			# Используем .get() с пустым значением по умолчанию
			coin_id_l = coin.get('id', '').lower()
			coin_sym_l = coin.get('symbol', '').lower()
			coin_name_l = coin.get('name', '').lower()
			if query in coin_id_l or query in coin_sym_l or query in coin_name_l:
				# Проверяем, что монета еще не в списке найденных
				if not any(f['id'] == coin.get('id') for f in found):
					found.append(coin)
					if len(found) >= 20:
						break

		if found:
			answ = '*Результаты поиска:*\n'
			for i in found:
				name = i.get('name', 'N/A')
				symbol = i.get('symbol', 'N/A').upper()
				coin_id = i.get('id', 'N/A')
				# Замена f-string на .format() с Markdown
				answ += '*{}* ({}) - `{}`\n'.format(name, symbol, coin_id)
			if len(found) >= 20:
				answ += "\n_(Показаны первые 20 совпадений)_"
			say(msg, answ)
		else:
			# Замена f-string на .format()
			say(msg, 'Ничего не найдено по запросу "{}".'.format(query))
		return

	elif command == '/info':
		if not args:
			say (msg, "Информацию по какому ID монеты показать? `/info <id>`")
			return
		coin_id_or_sym = args[0].lower()
		coin_id = get_id_by_string(coin_id_or_sym)

		if not coin_id:
			# Замена f-string на .format()
			say(msg, 'Не удалось найти монету по "{}". Попробуйте сначала `/search {}`.'.format(coin_id_or_sym, coin_id_or_sym))
			return

		try:
			answ = get_info_from_id(coin_id)
			say(msg, answ)
		except Exception as err:
			# Замена f-string на .format()
			print("Ошибка при получении /info для {}: {}".format(coin_id, err))
			print(traceback.format_exc())
			# Замена f-string на .format()
			say(msg, 'Ошибка обработки данных для ID {}.'.format(coin_id))
		return

	elif command == '/mine':
		try:
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
			# Замена f-string на .format()
			say(msg, '{}\n_(при {} кВт/Гх и {} руб/кВтч)_'.format(mine_info, power_consumption, power_price))
		except Exception as err:
			# Замена f-string на .format()
			print("Ошибка в команде /mine: {}".format(err))
			print(traceback.format_exc())
			say(msg, "Ошибка при расчете доходности майнинга.")
		return

	elif command == '/reload':
		usercheck = None # Инициализируем
		try:
			usercheck = msg.get('from', {}).get('username')
			if usercheck != "Brakhma":
				say(msg, "Permission denied!")
				# Замена f-string на .format()
				print('{} TRIES TO RELOAD!'.format(user_name(msg)))
				return
			say(msg, "Перезагружаюсь...")
			os.system("git pull")
			stopthreads()
			run = False # Сигнал основному циклу для завершения
		except Exception as err:
			print("Ошибка при выполнении /reload:")
			print(traceback.format_exc())
			say(msg, "Ошибка при перезагрузке.")
		return

	# --- Команды фонда (OKEx) ---
	elif command == '/fund':
		if okex is None:
			say(msg, "Функционал OKEx отключен (модуль не найден).")
			return

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
		my_cut_amount = 0
		user_id = msg['from']['id']

		for item in shares:
			if isinstance(item, list) and len(item) == 2:
				try:
					share_user_id = int(item[0])
					share_amount = int(item[1])
					shares_total += share_amount
					if share_user_id == user_id:
						my_cut_amount = share_amount
				except (ValueError, TypeError):
					# Замена f-string на .format()
					print("Неверный формат доли в fund_shares: {}".format(item))
					continue
			else:
				# Замена f-string на .format()
				print("Неверный формат записи в fund_shares: {}".format(item))
				continue

		if shares_total == 0:
			say(msg, "В фонде нет валидных долей.")
			return

		if my_cut_amount == 0:
			say(msg, 'Таки тьфу на тебя, твоей доли нет.')
			return

		perc = round((my_cut_amount / shares_total) * 100, 2)

		fund_str = '*Структура фонда:*\n'
		total_eq_usd = 0

		# Проверяем наличие ключей OKEx
		if not (okex_apikey and okex_secret and okex_passphrase):
			fund_str += "ОШИБКА: Не найдены или не заданы ключи API для OKEx в settings.py\n"
		else:
			try:
				ok_instance = okex.okex(api_key=okex_apikey, api_secret=okex_secret, passphrase=okex_passphrase)
				# !!! ВАЖНО: Адаптировать под OKEx v5 API !!!
				# Примерный код для v5 (требует установки python-okx):
				# from okx_python.Account import AccountAPI
				# accountAPI = AccountAPI(okex_apikey, okex_secret, okex_passphrase, False, "0") # 0 - реальный счет, 1 - демо
				# result = accountAPI.get_account_balance()
				# if result['code'] == '0':
				#    data = result.get('data', [{}])[0]
				#    total_eq_usd = float(data.get('totalEq', 0))
				#    details = data.get('details', [])
				#    for i in details:
				#        fund_str += "{}: {}\n".format(i.get('ccy', 'N/A'), i.get('cashBal', 'N/A'))
				# else:
				#     fund_str += "Ошибка API OKEx (Balance): {}\n".format(result.get('msg', 'Неизвестная ошибка'))

				# Заглушка, пока нет v5
				fund_str += "ОШИБКА: Функция /fund требует обновления для OKEx v5 API\n"
				print("ПРЕДУПРЕЖДЕНИЕ: Код OKEx не адаптирован под v5 API!")


				# --- Получение ордеров (тоже требует адаптации для v5) ---
				# from okx_python.Trade import TradeAPI
				# tradeAPI = TradeAPI(...)
				# orders_result = tradeAPI.get_order_list(instType="SPOT") # Пример для спота
				# if orders_result['code'] == '0':
				#    orders = orders_result.get('data', [])
				#    if orders: fund_str+='*Открытые ордера:*\n'
				#    for i in orders:
				#        fund_str += "{} {} {} x {}\n".format(i.get('instId'), i.get('side'), i.get('sz'), i.get('px','N/A'))
				# else:
				#     fund_str += "Ошибка API OKEx (Orders): {}\n".format(orders_result.get('msg', 'Неизвестная ошибка'))

			except NameError: # Если okex не импортирован
				fund_str += "ОШИБКА: Не найден модуль okex.\n"
			except Exception as e:
				# Замена f-string на .format()
				fund_str += "Ошибка при получении данных с OKEx: {}\n".format(e)
				# Замена f-string на .format()
				print("Ошибка OKEx: {}".format(e))
				traceback.print_exc()

		fund_str += '*Примерная оценка активов (USD):*\n'
		fund_str += '~{:.2f} $\n'.format(total_eq_usd)

		usd_to_rub_rate = None
		try:
			req = cg.get_price(ids='tether', vs_currencies='rub')
			if 'tether' in req and 'rub' in req['tether']:
				usd_to_rub_rate = float(req['tether']['rub'])
				total_eq_rub = total_eq_usd * usd_to_rub_rate
				fund_str += '~{:.2f} ₽\n'.format(total_eq_rub)
			else:
				fund_str += "(Не удалось получить курс RUB)\n"
		except Exception as e:
			# Замена f-string на .format()
			print("Ошибка получения курса USDT/RUB: {}".format(e))
			fund_str += "(Не удалось получить курс RUB)\n"


		fund_str += '*Твоя доля:*\n'
		my_cut_usd = total_eq_usd * (my_cut_amount / shares_total) if shares_total else 0
		fund_str += '~{:.2f} $ ({}%)\n'.format(my_cut_usd, perc)
		if usd_to_rub_rate:
			my_cut_rub = total_eq_rub * (my_cut_amount / shares_total) if shares_total else 0
			fund_str += '~{:.2f} ₽ ({}%)\n'.format(my_cut_rub, perc)

		fund_str += '\n\*без учёта комиссий за конвертацию и вывод.'
		say(msg, fund_str)
		return

	elif command == '/add_shares':
		if okex is None: # Проверяем доступность модуля
			say(msg, "Функционал OKEx отключен.")
			return

		usercheck = None
		try:
			usercheck = msg.get('from', {}).get('username')
			if usercheck != "Brakhma":
				say(msg, "Permission denied!")
				return
			if not args or len(args) < 2:
				say(msg, "Использование: `/add_shares <user_id> <количество>`")
				return

			new_user_id_str = args[0]
			new_shares_amount_str = args[1]

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

			found_user = False
			for i in range(len(shares)):
				if isinstance(shares[i], list) and len(shares[i]) == 2:
					try:
						if int(shares[i][0]) == new_user_id:
							shares[i][1] = str(int(shares[i][1]) + new_shares_amount)
							found_user = True
							break
					except (ValueError, TypeError):
						# Замена f-string на .format()
						print("Некорректная запись в shares при обновлении: {}".format(shares[i]))
						continue
				else:
					# Замена f-string на .format()
					print("Некорректный формат элемента в shares: {}".format(shares[i]))
					continue


			if not found_user:
				shares.append([str(new_user_id), str(new_shares_amount)])

			if set_setting('fund_shares', json.dumps(shares)):
				# Замена f-string на .format()
				say(msg, 'Добавлено/обновлено {} долей для user_id {}.\nТекущие доли: {}'.format(new_shares_amount, new_user_id, shares))
			else:
				say(msg, 'Ошибка сохранения данных о долях.')
			return
		except Exception as err:
			# Замена f-string на .format()
			say(msg, 'Ошибка при добавлении долей: {}'.format(err))
			print(traceback.format_exc())
		return

	# --- Обработка конвертера и общих паттернов ---

	# Используем text (обработанный) для регулярок
	converter_match = re.match(r'^((\d+([.,]\d+)?))\s+([a-zA-Z0-9.-]+)\s+to\s+([a-zA-Z0-9.-]+)$', text.lower())
	if converter_match:
		amount_str = converter_match.group(1).replace(',', '.')
		cur1_str = converter_match.group(4)
		cur2_str = converter_match.group(5)
		try:
			amount = float(amount_str)
			answ = converter(amount, cur1_str, cur2_str)
			say(msg, answ)
		except ValueError:
			say(msg, "Неверный формат числа.")
		except Exception as e:
			# Замена f-string на .format()
			print("Ошибка в конвертере: {}".format(e))
			print(traceback.format_exc())
			say(msg, "Ошибка при конвертации валют.")
		return

	# Обработка регулярок из словаря
	for pair in reg_answers:
		if pair.get('reg') and pair.get('answers'): # Проверяем наличие ключей
			try:
				if pair['reg'].search(text.lower()):
					answer = random.choice(pair['answers'])
					say(msg, answer)
					return
			except Exception as e:
				# Замена f-string на .format()
				print("Ошибка при проверке регулярки {}: {}".format(pair.get('reg'), e))
				continue # Пропускаем проблемную регулярку

	# Если ни одна команда или регулярка не подошла
	# print("Неизвестная команда или текст: {}".format(text))

# --- Инициализация и Запуск ---

if not TOKEN:
	print("Токен бота не задан. Выход.")
	sys.exit(1)

bot = YourBot(TOKEN)
# Замена f-string на .format()
print (Fore.YELLOW + bot.getMe()['first_name']+' (@'+bot.getMe()['username']+')'+Fore.WHITE)

def stopthreads():
	print("Остановка фоновых потоков...")
	stopped_count = 0
	for thing in threading.enumerate():
		# Проверяем, что это таймер и он еще "жив"
		if isinstance(thing, threading.Timer) and thing.is_alive():
			# Замена f-string на .format()
			print("Отмена таймера: {}".format(thing.name))
			thing.cancel()
			stopped_count += 1
	time.sleep(1) # Даем время на отмену
	# Проверка оставшихся
	remaining_timers = [t.name for t in threading.enumerate() if isinstance(t, threading.Timer) and t.is_alive()]
	if remaining_timers:
		# Замена f-string на .format()
		print("Не удалось остановить таймеры: {}".format(remaining_timers))
	else:
		print ("Все {} таймеров успешно отменены.".format(stopped_count))


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
	if not current_prices:
		return

	# Замена f-string на .format()
	print("{} - Обработка алертов для {} чатов...".format(datetime.now().strftime('%H:%M:%S'), len(chat_alerts_map)))
	active_alerts_count = 0

	for chat_id, alerts in chat_alerts_map.items():
		if not alerts: continue

		msg = {'chat': {'id': chat_id}}
		alerts_to_update = []

		for alert in alerts:
			# Безопасное получение данных из алерта
			coin_id = alert.get('valute')
			old_price_str = alert.get('price')
			old_time_str = alert.get('time')
			porog_str = alert.get('porog')

			# Пропускаем некорректные алерты
			if not all([coin_id, old_price_str, old_time_str, porog_str]):
				# Замена f-string на .format()
				print("Пропуск некорректного алерта в чате {}: {}".format(chat_id, alert))
				continue

			active_alerts_count += 1

			# Получаем текущую цену для монеты
			price_data = current_prices.get(coin_id)
			if not isinstance(price_data, dict) or 'usd' not in price_data or price_data['usd'] is None:
				# print("Нет текущей цены для {} в чате {}".format(coin_id, chat_id)) # Отладка
				continue

			try:
				old_price = float(old_price_str)
				porog = float(porog_str)
				current_price = float(price_data['usd'])

				if current_price == 0 or old_price == 0: continue # Избегаем деления на ноль

				change_percent = ((current_price - old_price) / old_price) * 100

				if abs(change_percent) >= porog:
					if change_percent > 0: chg_str = '▲'
					else: chg_str = '▼'

					try:
						old_time = datetime.strptime(old_time_str, "%d.%m.%Y %H:%M:%S")
						timediff = datetime.now() - old_time
						if timediff.days > 0:
							# Замена f-string на .format()
							strtd = '{} д.'.format(timediff.days)
						elif (timediff.seconds // 3600) > 0:
							# Замена f-string на .format()
							strtd = '{} ч.'.format(timediff.seconds // 3600)
						else:
							mins = (timediff.seconds // 60) % 60
							# Замена f-string на .format()
							strtd = '{} мин.'.format(max(1, mins))
					except ValueError:
						strtd = 'недавно'

					coin_sym = get_sym_by_id(coin_id) or coin_id
					price_str = "{:.8f}".format(current_price).rstrip('0').rstrip('.')
					old_price_str_fmt = "{:.8f}".format(old_price).rstrip('0').rstrip('.')

					# Замена f-string на .format()
					res_str = "{0} {1:.1f}% {2} за {3}\n`{4}` → `{5}` USD".format(
						coin_sym, abs(change_percent), chg_str, strtd, old_price_str_fmt, price_str
					)

					say(msg, res_str)

					alerts_to_update.append({
						'chat_id': chat_id,
						'coin_id': coin_id,
						'porog': porog, # Передаем числовой порог
						'current_price': current_price
					})

			except (ValueError, KeyError, TypeError) as e:
				# Замена f-string на .format()
				print("Ошибка обработки алерта {} для чата {}: {}".format(alert, chat_id, e))
				continue

		if alerts_to_update:
			# Замена f-string на .format()
			print("Обновление {} сработавших алертов для чата {}...".format(len(alerts_to_update), chat_id))
			updated_count = 0
			failed_count = 0
			update_msg = {'chat': {'id': chat_id}} # Используем временный msg
			for update_data in alerts_to_update:
				# Передаем числовой порог в set_alert
				if set_alert(update_msg, update_data['coin_id'], float(update_data['porog']), update_data['current_price']):
					updated_count += 1
				else:
					failed_count += 1
			# Замена f-string на .format()
			print("Обновлено: {}, Ошибки: {} для чата {}".format(updated_count, failed_count, chat_id))

	# Замена f-string на .format()
	print("{} - Обработано {} активных алертов.".format(datetime.now().strftime('%H:%M:%S'), active_alerts_count))

def refresh_coins_list_if_needed(force_refresh=False):
	global coins_list, last_coins_list_refresh_time
	now = datetime.now()
	# Проверяем, прошло ли достаточно времени или это принудительное обновление
	should_refresh = force_refresh or not last_coins_list_refresh_time or (now - last_coins_list_refresh_time > coins_list_refresh_interval)

	if should_refresh:
		# Замена f-string на .format()
		print("{} - Обновление списка монет...".format(datetime.now().strftime('%H:%M:%S')))
		try:
			new_coins_list = cg.get_coins_list()
			if isinstance(new_coins_list, list) and new_coins_list: # Проверка типа и что список не пустой
				old_list_len = len(coins_list)
				coins_list = new_coins_list
				last_coins_list_refresh_time = now
				# Сохраняем актуальный список в БД
				if set_setting('coins_list', json.dumps(coins_list)):
					# Замена f-string на .format()
					print("Список монет обновлен ({} -> {} монет) и сохранен в БД.".format(old_list_len, len(coins_list)))
				else:
					print("Список монет обновлен ({} -> {} монет), но не удалось сохранить в БД!".format(old_list_len, len(coins_list)))
				return True
			else:
				# Замена f-string на .format()
				print("Ошибка: Получен пустой или некорректный список монет от API (тип: {}).".format(type(new_coins_list)))
				# Не обновляем время, чтобы попробовать снова позже
				return False
		except (requests.exceptions.RequestException, ValueError) as e:
			if isinstance(e, ValueError) and '429' in str(e):
				# Замена f-string на .format()
				print("{} - Rate limit при обновлении списка монет!".format(datetime.now().strftime('%H:%M:%S')))
			else:
				# Замена f-string на .format()
				print("Ошибка при обновлении списка монет: {}".format(e))
			return False
	else:
		# print("Используется кэшированный список монет.") # Отладка
		return True # Список актуален, ничего не делали

# --- Функция get_id_by_string с хардкодом и разрешением через market_cap ---
def get_id_by_string(search_string):
	global coins_list, cg
	search_string_lower = search_string.lower()

	# Шаг 0: Хардкод
	if search_string_lower == 'btc':
		return 'bitcoin'
	# if search_string_lower == 'eth':
	#    return 'ethereum'

	# Шаг Pre: Проверка и загрузка coins_list
	if not coins_list:
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

	# Шаг 1: Поиск по ID
	for coin in coins_list:
		coin_id = coin.get('id')
		if coin_id and coin_id.lower() == search_string_lower:
			return coin['id']

	# Шаг 2: Поиск по символу
	matches_coins = []
	for coin in coins_list:
		coin_symbol = coin.get('symbol')
		coin_id = coin.get('id')
		if coin_symbol and coin_id and coin_symbol.lower() == search_string_lower:
			matches_coins.append(coin)

	# Шаг 3: Обработка результатов
	if len(matches_coins) == 1:
		return matches_coins[0]['id']
	elif len(matches_coins) > 1:
		# Разрешение конфликта
		conflicting_ids = [c['id'] for c in matches_coins]
		# Замена f-string на .format()
		setting_key = "symbol_resolution_{}".format(search_string_lower)
		# Замена f-string на .format()
		print("Обнаружен конфликт символа '{}': {}. ".format(search_string_lower, conflicting_ids))

		# Проверка БД
		saved_resolution = get_setting(setting_key)
		if saved_resolution:
			if any(c['id'] == saved_resolution for c in matches_coins):
				# Замена f-string на .format()
				print("Используется сохраненное разрешение из БД: '{}' -> '{}'".format(search_string_lower, saved_resolution))
				return saved_resolution
			else:
				# Замена f-string на .format()
				print("Сохраненное разрешение '{}' для '{}' больше не актуально. Попытка нового разрешения.".format(saved_resolution, search_string_lower))

		# Получаем market cap из API
		print("Попытка разрешения по market cap через API...")
		best_id = None
		max_market_cap = -1

		try:
			# Используем cg.get_coins_markets (исправлено с предыдущей версии)
			market_data = cg.get_coins_markets(ids=conflicting_ids, vs_currency='usd')

			if not market_data:
				 print("Ошибка: API get_coins_markets вернул пустой результат.")
				 best_id = conflicting_ids[0]
				 print("Не удалось получить данные о капитализации. Возвращаем первый ID: {}".format(best_id))
			else:
				# Ищем максимум
				for data in market_data:
					current_id = data.get('id')
					current_cap = data.get('market_cap')
					# print("  Проверка: {}, Капитализация: {}".format(current_id, current_cap)) # Отладка
					if current_id and current_cap is not None:
						# Сравниваем с максимальной найденной капитализацией
						if current_cap > max_market_cap:
							max_market_cap = current_cap
							best_id = current_id

				if best_id:
					# Замена f-string на .format()
					print("Выбран ID '{}' с максимальной капитализацией ({}).".format(best_id, max_market_cap))
					if set_setting(setting_key, best_id):
						# Замена f-string на .format()
						print("Разрешение для '{}' -> '{}' сохранено в БД.".format(search_string_lower, best_id))
					else:
						# Замена f-string на .format()
						print("Ошибка сохранения разрешения для '{}' в БД.".format(search_string_lower))
				else:
					best_id = conflicting_ids[0]
					# Замена f-string на .format()
					print("Не удалось определить ID с максимальной капитализацией. Возвращаем первый ID: {}".format(best_id))

		except (requests.exceptions.RequestException, ValueError, KeyError) as e:
			# Замена f-string на .format()
			print("Ошибка API при получении market cap для разрешения конфликта '{}': {}".format(search_string_lower, e))
			if isinstance(e, ValueError) and '429' in str(e):
				print("Сработал Rate Limit API!")
				time.sleep(10)
			best_id = conflicting_ids[0]
			# Замена f-string на .format()
			print("Возвращаем первый ID из-за ошибки API: {}".format(best_id))
		except Exception as e:
			# Замена f-string на .format()
			print("Неожиданная ошибка при разрешении конфликта для '{}': {}".format(search_string_lower, e))
			traceback.print_exc()
			best_id = conflicting_ids[0]
			# Замена f-string на .format()
			print("Возвращаем первый ID из-за неожиданной ошибки: {}".format(best_id))

		return best_id # Возвращаем лучший или первый

	else: # len(matches_coins) == 0
		# Шаг 4: Поиск по имени (опционально)
		# ...
		return False # Ничего не найдено

# --- Функция get_sym_by_id ---
def get_sym_by_id(coin_id):
	global coins_list
	if not coin_id: return None # Проверка на пустой ID
	coin_id_lower = coin_id.lower()

	if not coins_list: # Проверка кэша
		# print("Warning: get_sym_by_id called with empty coins_list cache.") # Отладка
		saved_list_json = get_setting('coins_list')
		if saved_list_json:
			try: coins_list = json.loads(saved_list_json)
			except json.JSONDecodeError: return None
		else: return None

	for coin in coins_list:
		if coin.get('id', '').lower() == coin_id_lower:
			return coin.get('symbol', '').upper() # Возвращаем символ в верхнем регистре
	return None

# --- Функция converter ---
def converter(amount, cur1_str, cur2_str):
	cur1_id = get_id_by_string(cur1_str)
	cur1_str_upper = cur1_str.upper() # Для вывода
	cur2_str_upper = cur2_str.upper() # Для вывода

	if not cur1_id:
		# Проверка на фиат cur1
		vses = None
		try: vses = cg.get_supported_vs_currencies()
		except Exception as e: print("Ошибка получения supported_vs_currencies: {}".format(e))

		if vses and cur1_str.lower() in vses:
			# cur1 - фиат, ищем cur2 (крипта)
			cur2_id = get_id_by_string(cur2_str)
			if not cur2_id: return "Не знаю вторую валюту: {}".format(cur2_str)

			try:
				req = cg.get_price(ids=cur2_id, vs_currencies=cur1_str.lower())
				if cur2_id in req and cur1_str.lower() in req[cur2_id]:
					rate = float(req[cur2_id][cur1_str.lower()])
					if rate == 0: return "Не удалось получить курс {} к {} (курс равен 0).".format(cur2_id, cur1_str_upper)
					result = amount / rate
					cur2_sym = get_sym_by_id(cur2_id) or cur2_id
					# Замена f-string на .format()
					return "{:.2f} {} = {:.8f} {} ({})".format(amount, cur1_str_upper, result, cur2_sym, cur2_id)
				else:
					# Замена f-string на .format()
					return "Не удалось получить курс {} к {}.".format(cur2_id, cur1_str_upper)
			except (requests.exceptions.RequestException, ValueError, KeyError, TypeError) as e:
				# Замена f-string на .format()
				print("Ошибка конвертации {} к {}: {}".format(cur2_id, cur1_str, e))
				# Замена f-string на .format()
				return "Ошибка получения курса {} к {}.".format(cur2_id, cur1_str_upper)
		else:
			# Замена f-string на .format()
			return "Не знаю первую валюту: {}".format(cur1_str)

	# cur1 - крипта
	cur1_sym = get_sym_by_id(cur1_id) or cur1_id
	cur2_id = get_id_by_string(cur2_str)

	if cur2_id: # cur2 - крипта
		try:
			# Конвертация крипта -> крипта через USD
			ids_to_fetch = "{},{}".format(cur1_id, cur2_id)
			rates = cg.get_price(ids=ids_to_fetch, vs_currencies='usd')
			price1_usd = rates.get(cur1_id, {}).get('usd')
			price2_usd = rates.get(cur2_id, {}).get('usd')

			if price1_usd is None or price2_usd is None:
				cur2_sym_temp = get_sym_by_id(cur2_id) or cur2_id
				# Замена f-string на .format()
				return "Не удалось получить курсы к USD для {} или {}.".format(cur1_sym, cur2_sym_temp)
			if price2_usd == 0:
				cur2_sym_temp = get_sym_by_id(cur2_id) or cur2_id
				# Замена f-string на .format()
				return "Курс {} к USD равен 0, конвертация невозможна.".format(cur2_sym_temp)

			result = amount * (price1_usd / price2_usd)
			cur2_sym = get_sym_by_id(cur2_id) or cur2_id
			# Замена f-string на .format()
			return "{:.8f} {} ({}) = {:.8f} {} ({})".format(amount, cur1_sym, cur1_id, result, cur2_sym, cur2_id)

		except (requests.exceptions.RequestException, ValueError, KeyError, TypeError) as e:
			cur2_sym_temp = get_sym_by_id(cur2_id) or cur2_id
			# Замена f-string на .format()
			print("Ошибка конвертации {} к {}: {}".format(cur1_id, cur2_id, e))
			# Замена f-string на .format()
			return "Ошибка получения курсов для конвертации {} в {}.".format(cur1_sym, cur2_sym_temp)

	else: # cur2 - возможно, фиат
		vses = None
		try: vses = cg.get_supported_vs_currencies()
		except Exception as e: print("Ошибка получения supported_vs_currencies: {}".format(e))

		if vses and cur2_str.lower() in vses:
			# cur2 - фиат
			try:
				req = cg.get_price(ids=cur1_id, vs_currencies=cur2_str.lower())
				if cur1_id in req and cur2_str.lower() in req[cur1_id]:
					rate = float(req[cur1_id][cur2_str.lower()])
					result = amount * rate
					# Форматируем фиат
					result_str = "{:.2f}".format(result) if cur2_str.lower() in ['usd', 'eur', 'gbp', 'rub'] else "{:.8f}".format(result)
					# Замена f-string на .format()
					return "{:.8f} {} ({}) = {} {}".format(amount, cur1_sym, cur1_id, result_str, cur2_str_upper)
				else:
					# Замена f-string на .format()
					return "Не удалось получить курс {} к {}.".format(cur1_sym, cur2_str_upper)
			except (requests.exceptions.RequestException, ValueError, KeyError, TypeError) as e:
				# Замена f-string на .format()
				print("Ошибка конвертации {} к {}: {}".format(cur1_id, cur2_str, e))
				# Замена f-string на .format()
				return "Ошибка получения курса {} к {}.".format(cur1_sym, cur2_str_upper)
		else:
			# Замена f-string на .format()
			return "Не знаю вторую валюту: {}".format(cur2_str)

# --- Функция initial_load_coins ---
def initial_load_coins():
	global coins_list, last_coins_list_refresh_time
	print("Загрузка списка монет при старте...")
	saved_list_json = get_setting('coins_list')
	loaded_from_db = False
	if saved_list_json:
		try:
			coins_list = json.loads(saved_list_json)
			# Устанавливаем время последнего обновления так, чтобы оно было не слишком старым
			last_coins_list_refresh_time = datetime.now() - timedelta(minutes=coins_list_refresh_interval.total_seconds()/120) # Примерно половина интервала
			# Замена f-string на .format()
			print("Список монет ({}) загружен из БД.".format(len(coins_list)))
			loaded_from_db = True
		except json.JSONDecodeError:
			print("Ошибка декодирования списка монет из БД. Загрузка из API...")
			coins_list = []
			last_coins_list_refresh_time = None

	if not loaded_from_db:
		refresh_coins_list_if_needed(force_refresh=True)

initial_load_coins()

# --- Функция filter_bullshit ---
def filter_bullshit(coin_info_str):
	# Принимает строку Markdown
	strikes = []
	coin_info_lower = coin_info_str.lower()
	stop_words = ['meta','shiba','inu','meme','zilla','doge', 'verse', 'floki', 'baby', 'elon', 'potter', 'obama', 'sonic', 'cat', 'dog', 'moon']

	for word in stop_words:
		# Простая проверка вхождения слова
		if word in coin_info_lower:
			# Замена f-string на .format()
			if 'word: {}'.format(word) not in strikes: strikes.append('word: {}'.format(word))

	if "binance smart chain" in coin_info_lower or 'bscscan.com' in coin_info_lower: strikes.append('L2 BSC?')
	if "polygon ecosystem" in coin_info_lower or 'polygonscan.com' in coin_info_lower: strikes.append('L2 Polygon?')
	if "etherscan.io" in coin_info_lower or "ethplorer.io" in coin_info_lower: strikes.append('L2 ETH?')
	if "solscan.io" in coin_info_lower or "explorer.solana.com" in coin_info_lower: strikes.append('L2 SOL?')

	if "non-fungible tokens (nft)" in coin_info_lower:
		if 'NFT' not in strikes: strikes.append('NFT?')

	if '*github:*' not in coin_info_lower and '*bitbucket:*' not in coin_info_lower:
		if 'closed source?' not in strikes: strikes.append('closed source?')

	return strikes

# --- Функция recheck_list ---
def recheck_list():
	global coins_list

	# Список монет должен быть уже обновлен через refresh_coins_list_if_needed() в getcourses_loop
	if not coins_list:
		print("Проверка новых монет пропущена: список монет пуст.")
		return

	ocl_json = get_setting('old_coins_list') # Используем отдельную настройку
	old_coins_map = {}
	if ocl_json:
		try:
			old_coins_list_data = json.loads(ocl_json)
			if isinstance(old_coins_list_data, list):
				old_coins_map = {coin.get('id'): coin for coin in old_coins_list_data if coin.get('id')}
			else:
				print("Ошибка формата old_coins_list в БД.")
		except json.JSONDecodeError:
			print("Ошибка декодирования old_coins_list из БД.")

	# Сравнение
	diff = []
	current_coins_map = {coin.get('id'): coin for coin in coins_list if coin.get('id')}
	for coin_id, coin_data in current_coins_map.items():
		if coin_id not in old_coins_map:
			diff.append(coin_data)

	if diff:
		# Замена f-string на .format()
		print(Fore.GREEN + "{} - Найдено {} новых монет!".format(datetime.now().strftime('%H:%M:%S'), len(diff)) + Fore.WHITE)

		na_json = get_setting('newalerts')
		newalerts_chat_ids = []
		if na_json:
			try:
				loaded_ids = json.loads(na_json)
				# Убедимся, что это список чисел/строк
				if isinstance(loaded_ids, list):
					newalerts_chat_ids = [str(chat_id) for chat_id in loaded_ids] # Приводим к строкам на всякий случай
			except json.JSONDecodeError: pass

		if newalerts_chat_ids:
			# Замена f-string на .format()
			print("Отправка уведомлений о новых монетах в чаты: {}".format(newalerts_chat_ids))
			# Замена f-string на .format()
			base_message = "*Новые монеты ({})*\n".format(len(diff))
			full_diff_str = ""
			coins_processed = 0

			for coin_data in diff:
				coin_id = coin_data.get('id')
				if not coin_id: continue

				nfo = get_info_from_id(coin_id) # Получаем инфо
				fb = filter_bullshit(nfo) # Фильтруем

				coin_block = ""
				# Фильтруем булшит (можно настроить строгость)
				if len(fb) >= 2 and 'closed source?' in fb:
					# Берем только первую строку
					coin_block += "{}\n".format(nfo.splitlines()[0] if '\n' in nfo else nfo)
					coin_block += "*Предположительно буллшит:* " + ', '.join(fb) + "\n"
				else:
					coin_block += nfo # Полная инфа
					if fb:
						coin_block += "\n*Возможные признаки буллшита:* " + ', '.join(fb) + "\n"

				coin_block += "====================\n"

				# Проверка длины сообщения
				if len(base_message) + len(full_diff_str) + len(coin_block) > 4000:
					for chat_id in newalerts_chat_ids:
						say({'chat': {'id': chat_id}}, base_message + full_diff_str, silent=True)
					full_diff_str = coin_block
					time.sleep(0.5)
				else:
					full_diff_str += coin_block

				coins_processed += 1
				if coins_processed % 5 == 0: time.sleep(0.2)

			# Отправляем остаток
			if full_diff_str:
				# Определяем, нужно ли добавлять заголовок
				final_message = base_message + full_diff_str if coins_processed == len(diff) else full_diff_str
				for chat_id in newalerts_chat_ids:
					say({'chat': {'id': chat_id}}, final_message, silent=True)

		# Обновляем 'old_coins_list' в БД
		if set_setting('old_coins_list', json.dumps(coins_list)):
			print("Список old_coins_list в БД обновлен.")
		else:
			print("Ошибка обновления old_coins_list в БД.")
	# else: print ('Нет новых монет.') # Отладка


# --- Запуск основного цикла и потоков ---

run = True # Определяем до первого вызова getcourses_loop

# Запускаем основной цикл получения курсов в отдельном потоке
getcourses_loop()

# Основной цикл программы
usercheck = None # Инициализируем переменную для /reload
try:
	print("Бот запущен. Ожидание сообщений...")

	# Запускаем message_loop в отдельном потоке
	message_loop_thread = threading.Thread(
		target=MessageLoop(bot, {'chat': bot.on_chat_message,
								 'edited_chat': bot.on_edited_chat_message}).run_forever,
		daemon=True # Поток завершится сам при выходе
	)
	message_loop_thread.start()
	print("Message loop запущен в отдельном потоке.")

	# Основной поток ждет сигнала остановки
	while run:
		# Проверяем, жив ли поток message_loop (на всякий случай)
		if not message_loop_thread.is_alive():
			print("ОШИБКА: Поток message_loop завершился!")
			run = False # Останавливаем бота
			break
		time.sleep(1)

	print("Завершение работы основного цикла...")

except KeyboardInterrupt:
	print("\nПолучено прерывание клавиатуры (Ctrl+C). Завершение работы...")
	run = False
	stopthreads()

except Exception as e:
	print("\nНепредвиденная ошибка в основном цикле:")
	print(traceback.format_exc())
	run = False
	stopthreads()

finally:
	time.sleep(2) # Даем время на завершение
	print("Закрытие соединения с БД...")
	try:
		if db: # Проверяем, что соединение еще есть
			db.commit()
			db.close()
	except Exception as db_err:
		# Замена f-string на .format()
		print("Ошибка при закрытии БД: {}".format(db_err))

	print("Выход.")
	# Запуск reload.sh, если была команда
	try:
		if not run and usercheck == "Brakhma":
			print("Выполнение reload.sh...")
			try:
				# Запускаем в фоне
				os.system("./reload.sh &")
			except Exception as reload_err:
				# Замена f-string на .format()
				print("Ошибка при запуске reload.sh: {}".format(reload_err))
	except NameError:
		pass # usercheck не был определен
