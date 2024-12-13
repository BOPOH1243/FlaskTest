#!/bin/bash

# Устанавливаем PYTHONPATH в текущее местоположение терминала
export PYTHONPATH=$(pwd):$PYTHONPATH

# Выводим результат для проверки
echo "PYTHONPATH установлен в: $PYTHONPATH"
