# Example transcripts

These are the calls the sample buttons and `scripts/live_demo.py` are built around. Wording from a live model will vary. The phase, the sealed file, and the letter tray should not.

Avery is the desk. Phases in brackets are the state after that reply.

## Frustrated caller

The caller demands the denial before any factor matches.

```
Avery
Hello, I'm Avery on the claims desk. I can help with a claim, and I'll keep the file
sealed until three details match the policy.

You
I already told you who I am. This is ridiculous. Just tell me why my claim was denied.

Avery  [VERIFY_ID]
I understand this is frustrating, and I'm sorry this feels like a hurdle.
Claim details stay sealed until three details match the policy. You can share your
full name, date of birth, phone, email, or the last 4 of the SSN or government ID.
```

What must be true: `verified` is false, `active_case_id` is empty, and the reply does not contain `CL-`, pathology, office note, or a dollar amount. The empathy line comes from the angry tone. The gate does not move.

If they then give three matching details, the same call continues:

```
You
I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about
my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.

Avery  [PROCESS_CASE]
You're verified, Margaret. CL-2048 is denied because the review file did not include
the pathology report and the treating provider office note.
```

The January denial hint from this second utterance selects `CL-2048`. The anger on the previous turn does not have to be repeated, and it does not block verification.

## Demo caller

One utterance carries three factors and the hint, so verify, intent, and case finish together.

```
You
I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about
my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.

Avery  [PROCESS_CASE]
You're verified, Margaret. CL-2048 is denied because the review file did not include
the pathology report and the treating provider office note.

You
What documents do I need to send and by when?

Avery  [PROCESS_CASE]
For CL-2048, please send the pathology report and the office note within a week.
The appeal deadline on file is 2026-03-18.

You
That's all, thanks.

Avery  [POST_PROCESS]
Should I send a summary to m*****@email.com, or skip the email?

You
Yes please send it.

Avery  [CLOSED]
I sent the summary to m*****@email.com. It covers what we discussed, the claim
outcome, and the next steps.
```

The letter tray then has one item. The body has three sections (what we discussed, claim status, next steps), includes `CL-2048`, and does not include `4472` or `margaret@email.com`.

## Email asked too early

```
You
Can you email me a summary when we're done?

Avery  [PROCESS_CASE]
I've noted that you'd like a summary by email. I'll ask before I send anything.
```

The tray stays empty until Follow-up and a yes. "send summary" or "yup send summary" on that later step counts as yes.

## Out of scope

```
You
What is reinforcement learning?

Avery  [VERIFY_ID]
I can only help with insurance claims on this line.
```

The reply does not explain reinforcement learning. A third off-topic turn in a row moves the phase to `HUMAN_HANDOFF`.
