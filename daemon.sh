#!/bin/bash
while [[ true ]]; do
  python backup_forks.py "$1"
  sleep 60m
done
