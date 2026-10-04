#!/bin/bash
AB="npx -y agent-browser@0.27.0 --session lsfa"
cd /tmp
before=$($AB get text '[data-testid="chatbot-console-thread"]' 2>/dev/null)
$AB find placeholder "Ask, or attach voice/image" fill "$1" >/dev/null
$AB find role button click --name "Send message" >/dev/null
for i in $(seq 1 90); do
  sleep 1
  now=$($AB get text '[data-testid="chatbot-console-thread"]' 2>/dev/null)
  n=$($AB get count '[data-testid="chatbot-console-typing"]' 2>/dev/null | tail -1)
  if [ "$n" = "0" ] && [ ${#now} -gt $(( ${#before} + ${#1} + 5 )) ]; then break; fi
done
sleep 1
now=$($AB get text '[data-testid="chatbot-console-thread"]' 2>/dev/null)
echo "${now:${#before}}" | sed '/^\(business_query\|casual\|v1\|trace\|file\)$/d;/^application\//d' | sed '/^$/d'
