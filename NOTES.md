# Notes

## How to run

1. Copy `cloud-flare.sh` to `cloud-flare.local.sh` and add your Cloudflare account ID and API token. This local file is git-ignored and should never be pushed to GitHub.
2. Run `python3 classify.py v1` to classify `comments.json` using `clef-flash`. It generates `results.html`.
3. Run `python3 classify.py v2` to classify `comments-v2.json` using the larger `clef` model. It generates `results-v2.html`.
4. Open the HTML reports in a browser. You can filter comments by label, check low-confidence predictions, sort by confidence, and search for specific comments.

No extra packages are needed because the program uses Python's standard library.




## Tools and models used

- **Cloudflare Workers AI:** Used `clef-flash` for v1 and the larger `clef` model for v2.
- **Claude (claude.ai):** For understanding the Python code, HTML reports and setting up Git and GitHub.
- **VS Code and Git/GitHub:** For editing code and checking results.
- **Prompt Engineering:** With help from AI, I could understand how to define the classification criteria. After looking at the errors in v1, I improved the `CRITERIA_V2` file to better identify personal attacks, stereotypes based on groups, constructive criticism and spam.
- **Manual checking:** The results were manually checked. Any instances where I disagreed with Clef’s classifications were written down in `MISTAKES.md` and `MISTAKES-v2.md`. I also thought about cases where the classification could be seen as a grey area depending on the context.

## What I felt about doing this
The easiest part was getting the project running and generating the reports once I had sorted out the Cloudflare API setup. The harder part was deciding whether a comment was actually toxic, spam, or acceptable. Some comments looked straightforward at first, but their meaning became clearer when I considered the context.
One thing I learned was that sarcasm does not always mean someone is being toxic. A person can give blunt feedback without personally attacking anyone, and someone quoting offensive language to report it should not be treated the same as someone using it to insult another person. I also realised that a survey or workshop link is not automatically spam. It is important to look at what the message is asking people to do and whether it seems genuine.
When I compared the two reports, I noticed that the larger model didn't give exactly the same results as the first one. It marked more comments as spam and toxic, but that doesn't necessarily mean it was more accurate. Going through the comments helped me realise how important context is when moderating content. Updating the criteria can help, but it doesn't fix every mistake. Some comments are still difficult to judge, especially when a genuine announcement looks suspicious or when sarcasm makes it hard to understand what someone really means. In these cases, having a human review the comment is still important.
Since I am interested in AI development, I found this task useful because it involved more than just running a model. I got to work with prompt engineering, review model outputs, identify possible false positives and false negatives, and improve the criteria based on the mistakes I found. It also showed me why human judgment is still important when building AI moderation systems.
I would be happy to do this kind of work as part of AI development because I enjoy understanding why a model gets something wrong and figuring out how to improve it. Checking every comment manually could get repetitive, though. Ideally, I would use AI to handle the easier cases and focus my attention on comments where the model is uncertain or where getting the decision wrong could have a bigger impact.