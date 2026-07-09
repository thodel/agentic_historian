#!/usr/bin/env bash
# Simple analysis script for SSRQ TTL corpus
TTL="/home/dh/resources/ssrq__fuseki_042810.ttl"

echo "--- SSRQ TTL Analysis ---"

echo "File size:"; ls -lh "$TTL"

echo "Line count:"; wc -l "$TTL"

PERSONS=$(grep -c "^<http://ssrq-sds-fds.ch/Register/#per" "$TTL")
ORGS=$(grep -c "^<http://ssrq-sds-fds.ch/Register/#org" "$TTL")

echo "Persons: $PERSONS"
echo "Organizations: $ORGS"

echo "\nSample first 2 person entries (saved in sample/):"
mkdir -p sample
# Extract first two person blocks
awk 'BEGIN{RS="\n\n"} /^<http:\/\/ssrq-sds-fds.ch\/Register\/#[p][e][r]/ {print > "sample/person_" ++c ".ttl"}' "$TTL" && echo "Saved sample/person_1.ttl and sample/person_2.ttl"

echo "--- Done ---"