from optparse import OptionParser
import urllib.request
import json
import re


def cry_fiat(crypto,fiat, tofiat = False, value = 0):
	#checking fiat and crypto
	if not check_crypto(crypto): return ('Invalid Crypto.')
	if not check_fiat(fiat): return ('Invalid Fiat.')

	#blockchain.info
	try:	
		request = urllib.request.Request('https://blockchain.info/ticker')
		response = urllib.request.urlopen(request, timeout = 20)
		received_data = (response.read()).decode('utf-8')
		results = json.loads(received_data)
		fi_btc = float(results[fiat.upper()]['last'])
		done = True
		if __name__ == "__main__": print(fiat+' = '+str(fi_btc)+' btc')
	except Exception as err:
		print(err)
	if crypto.upper() == 'BTC':
		cry_btc = 1
	else:
		#poloniex
		try:
			request = urllib.request.Request('https://poloniex.com/public?command=returnTicker')
			response = urllib.request.urlopen(request, timeout = 20)
			received_data = (response.read()).decode('utf-8')
			results = json.loads(received_data)
			cry_btc = float(results['BTC_'+crypto.upper()]['last'])
			if __name__ == "__main__": print(crypto+' = '+str(cry_btc)+' btc')
		except Exception as err:
			print(err)

	if tofiat:
		return (value*cry_btc*fi_btc)
	else:
		return ((value/fi_btc)/cry_btc)	

def cry_cry(cry1,cry2, value = 0):
	if not check_crypto(cry1): return ('Invalid Crypto. '+cry1)
	if not check_crypto(cry2): return ('Invalid Crypto. '+cry2)
	if cry1.lower() == cry2.lower(): return (value)
	
	#poloniex
	try:
		request = urllib.request.Request('https://poloniex.com/public?command=returnTicker')
		response = urllib.request.urlopen(request, timeout = 20)
		received_data = (response.read()).decode('utf-8')
		results = json.loads(received_data)
	except Exception as err:
		print(err)

	if cry1.upper() == 'BTC':
		cry_btc = float(results['BTC_'+cry2.upper()]['last'])
		if __name__ == "__main__": print(cry2+' = '+str(cry_btc)+' btc')
		return (value/cry_btc)
	elif cry2.upper() == 'BTC':
		cry_btc = float(results['BTC_'+cry1.upper()]['last'])
		if __name__ == "__main__": print(cry1+' = '+str(cry_btc)+' btc')
		return (value*cry_btc)
	else:
		cry1_btc = float(results['BTC_'+cry1.upper()]['last'])
		if __name__ == "__main__": print(cry1+' = '+str(cry1_btc)+' btc')
		cry2_btc = float(results['BTC_'+cry2.upper()]['last'])
		if __name__ == "__main__": print(cry2+' = '+str(cry2_btc)+' btc')
		return ((value*cry1_btc)/cry2_btc)

def check_crypto(curr):
	if curr.upper() == 'USDT': return False
	if curr.upper() == 'BTC': return True
	request = urllib.request.Request('https://poloniex.com/public?command=returnTicker')
	response = urllib.request.urlopen(request, timeout = 20)
	received_data = (response.read()).decode('utf-8')
	results = json.loads(received_data)
	alc = []
	for key in results.keys():
		if not key.startswith('BTC_'):continue
		crpt = (key.lower()).partition('btc_')[2]
		#print(crpt)
		alc.append(crpt)
	#print(curr)
	if curr in alc:
		return True
	else:
		return False

def check_fiat(curr):
	request = urllib.request.Request('https://blockchain.info/ticker')
	response = urllib.request.urlopen(request, timeout = 20)
	received_data = (response.read()).decode('utf-8')
	results = json.loads(received_data)
	alc = []
	for key in results.keys():
		#print(key)
		alc.append(key.lower())
	#print(curr)
	if curr in alc:
		return True
	else:
		return False

def convertor(constr):
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
	if check_crypto(cur1):
		if check_crypto(cur2):
			answ = cry_cry(cur1, cur2, value = num)
		elif check_fiat(cur2):
			answ = cry_fiat(cur1, cur2, tofiat = True, value = num)
		else:
			return ("Don't know about "+cur2)
	elif check_fiat(cur1):
		if check_crypto(cur2):
			answ = cry_fiat(cur2, cur1, tofiat = False, value = num)
		elif check_fiat(cur2):
			return ("Can't convert fiat to fiat. ")
		else:
			return ("Don't know about "+cur2)
	else:
		return ("Don't know about "+cur1)

	if isinstance(answ, str):
		return (answ) 
	else:
		return (str(answ)+' '+cur2)

if __name__ == "__main__":
	parser = OptionParser()
	parser.add_option('-c', '--crypto', dest='crypto',
					action='store',
					help="Crypto to convert")
	parser.add_option('-f', '--fiat', dest='fiat',
					action='store',
					help="Fiat to tconvert")
	parser.add_option('-v', '--value', dest='value',
					action='store',
					help="Value")
	parser.add_option('-s', '--string', dest='constr',
					action='store',
					help="Conversion string")
	options, args = parser.parse_args()

	if not options.crypto or not options.fiat:
		if not options.constr: 
			print('Enter crypto and fiat.')
			quit()
		else:
			a = convertor(options.constr)
	else:
		a = cry_fiat(options.crypto, options.fiat, value = float(options.value))
	
	print(a)