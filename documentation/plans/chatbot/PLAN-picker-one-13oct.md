# PLAN: PICKER-ONE, one customer picker for every entry path

Status: in progress (small fix track: chatbot only, no migration, no auth/RBAC change)

## Owner ask (13 Oct)

Two "Which one do you mean?" customer pickers look different: "1. HANLIM 2. HANLIM TRADING" (no
stamp) vs "1. HANLIM - no DO 2. HANLIM TRADING - no DO" (stamped), and the gate's "Which customer
do you mean? Please choose: 1. X (MCH, SRT) - has DO". Rule: reuse, never parallel paths.

## Build

Every customer disambiguation renders through one function with options stamped by one helper.
Details are filled in below as the lane is read and built.
