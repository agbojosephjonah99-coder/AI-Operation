# Deploying to Vercel

The web app runs on Vercel unchanged. Vercel loads `app` from `app.py`, and
`vercel.json` gives each request up to 300 seconds, the most the free
Hobby plan allows.

Once deployed, the app is on the public internet, so **a password is
required**. The site refuses to show any data until `APP_PASSWORD` and
`SECRET_KEY` are set.

## Before you start

1. **Change your keys if the old zip was shared.** The original project
   zip contained a live OpenAI key and Airtable token. Create new ones
   (OpenAI dashboard → API keys; Airtable → Builder hub → Personal access
   tokens) and delete the old ones.
2. **Commit `privacy.json`.** Run `python setup_privacy.py` locally and keep
   the file. It holds only field names and levels, no student data and no
   secrets. If you'd rather not commit it, paste its contents into a
   `PRIVACY_JSON` environment variable instead (step 3).
3. **Never upload `.env`.** `.gitignore` and `.vercelignore` already leave
   it out. Your keys go into Vercel's settings instead.

## Option A: GitHub (recommended, redeploys on every push)

1. Create a **private** GitHub repository and push this folder:

   ```bash
   git init
   git add .
   git status          # check that .env and .venv are NOT listed
   git commit -m "AI Operations Assistant"
   git branch -M main
   git remote add origin https://github.com/<you>/ai-operations-assistant.git
   git push -u origin main
   ```

2. In Vercel, click **Add New → Project**, import the repository, and leave
   the framework settings as they are (Vercel detects Flask).
3. Before clicking **Deploy**, open **Environment Variables** and add:

   | Name | Value |
   |---|---|
   | `OPENAI_API_KEY` | your new OpenAI key |
   | `Airtable_Personal_Access_Token` | your new Airtable token |
   | `Airtable_Base_ID` | `app...` |
   | `APP_PASSWORD` | a strong password for staff |
   | `SECRET_KEY` | a long random value (see below) |
   | `OPENAI_MODEL` | optional, defaults to `gpt-4.1-mini` |
   | `PRIVACY_JSON` | optional, only if `privacy.json` is not committed |

   Generate a `SECRET_KEY` with:

   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

   Any other setting from `.env.example` (e.g. `STUDENT_TABLE`) can be
   added the same way.
4. Click **Deploy**, open the URL Vercel gives you, and sign in with
   `APP_PASSWORD`.

## Option B: Vercel CLI

```bash
npm install -g vercel
vercel login
vercel link                       # create or link the project
vercel env add OPENAI_API_KEY production
vercel env add Airtable_Personal_Access_Token production
vercel env add Airtable_Base_ID production
vercel env add APP_PASSWORD production
vercel env add SECRET_KEY production
vercel --prod                     # deploy
```

Each `vercel env add` asks you to paste the value.

## After deploying

- **Changing a setting:** edit it in Project Settings → Environment
  Variables, then redeploy (Deployments → ⋯ → Redeploy). Settings only
  take effect on a new deployment.
- **Signing everyone out:** change `SECRET_KEY` and redeploy. This also
  clears any open conversations.
- **Logs:** Project → Logs shows errors, such as a wrong Airtable token.

## Things that work differently on Vercel

- **Conversation memory** is kept in the browser tab, signed by the server
  so it can't be altered. Reloading the page starts a new conversation.
- **Cached Airtable data** lives only as long as the server instance does.
  The first question after a quiet period may take a few seconds longer
  while tables download again. "Refresh from Airtable" clears the cache on
  the instance that handled the click.
- **Very large lists:** Vercel limits a response to about 4.5 MB, roughly
  tens of thousands of rows. Your current tables are far below that.
- **Where data passes through:** student records now travel through
  Vercel's servers on their way to the browser. By default these are in
  the US. You can choose a region under Project Settings → Functions.
  Check this fits your institution's data-protection obligations.
- **Plan:** Vercel's free Hobby plan is meant for personal, non-commercial
  use. For an organisation's internal tool, check Vercel's terms; you may
  need the Pro plan.
