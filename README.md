# moisha
Криптовалютный telegram бот.

Может подсказать примерный курс конвертации крипты в крипту и в фиат ('222 btc to usd', '600 rub to xrp')
Для каждого чата может выводить курсы интересующих валют /kurs и сигнализировать о крупных движниях курса. Валюты и пороги задаются командой /alert

Реализует работу со словарём регулярных выражений.

Зависимости:  
Python3  
telepot  
sqlite3  
colorama  

команды:
/kurs - выдаёт курсы (команда задана через словарь)  
/alert - задаёт интересующие валюты и порог уведомления  
/alerts - выдаёт список алертов  
/noalert - сбрасывает алерт  
/noalerts - сбрасывает все алерты  

Может работать через https прокси.

Для связи:  
telegram:@brakhma
