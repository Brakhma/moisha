#!/bin/bash
while read PID CPU REST < <(ps -eo pid,pcpu,args|grep [m]oisha)
      do sleep 2
      done
#python3 moisha.py
screen -S moisha -d -m python3 /home/brakhma/robots/moisha/moisha.py