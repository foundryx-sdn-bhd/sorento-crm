# UAC: chatbot queue fix (1 Oct)

- AC-1: ticket N+1 arriving after ticket N finished runs immediately (no QueueWait), even if `running` was overwritten by later tickets.
- AC-2: a predecessor that died without releasing (no heartbeat) is skipped within the stale window, not after the full wait.
- AC-3: a predecessor that is alive and heartbeating is waited for (no two turns of one contact at once) up to the cap.
- AC-4: on cap timeout the waiting turn still runs and answers; it never fails at `queued` with the generic error.
- AC-5: a turn that ran after a timeout never moves `done` past a ticket that is still running.
- AC-6: a failed release logs at ERROR naming contact and ticket.
- AC-7: a stock reply for codes A and B where A resolves with no rows says "No stock found for A" next to B's rows.
