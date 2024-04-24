#!/bin/sh

cd `dirname "$0"`
python3 setup.py clean --all
python3 setup.py bdist_wheel
