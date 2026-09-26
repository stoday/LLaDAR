# Publish and update the LLaDAR user guide

The public site is built only from `site/`. The workflow in `.github/workflows/pages.yml` uploads that directory as a GitHub Pages artifact, so the repository's internal `docs/` files are not included in the Pages artifact.

## First publication

1. Review the current local CLI source together with the English page in `site/index.html`, the Traditional Chinese page in `site/zh-TW/index.html`, and the workflow. These pages describe the current working tree; some commands may not be in the latest published package.
2. Commit the reviewed implementation and these five documentation/deployment files on a branch, then merge them into `main`. Publishing the pages before the matching CLI implementation would leave public examples that readers cannot run. The workflow deploys only pushes to `main`.
3. Open the repository on GitHub: **Settings → Pages → Build and deployment → Source → GitHub Actions**. Save the setting if GitHub asks you to.
4. Open **Actions → Publish user guide**. A successful run supplies the deployment URL. If changing the Pages source after the merge did not trigger a run, use **Run workflow** on the `main` branch.
5. Open `https://stoday.github.io/LLaDAR/` and `https://stoday.github.io/LLaDAR/zh-TW/`. Check the language links and code examples in both pages.

The repository must allow GitHub Actions and Pages deployment. The site is publicly readable after deployment. The workflow does not deploy the whole repository.

## Subsequent updates

Edit either HTML page or `site/assets/style.css` in a branch and merge it into `main`. Any push to `main` that changes `site/**` or the Pages workflow triggers a new deployment. Check **Actions → Publish user guide** for the result, then refresh the public URL.

For a local update from the repository root, after checking that your working tree is ready for a branch change:

```powershell
git switch main
git pull --ff-only origin main
git switch -c docs/update-user-guide
# Edit files in site/
git add site
git commit -m "Update LLaDAR user guide"
git push -u origin docs/update-user-guide
```

Open a pull request for that branch and merge it after review. If you use GitHub's web editor instead, edit the same `site/` files, commit to a new branch, and open a pull request.

## If the page is not visible

- Confirm **Settings → Pages → Source** is **GitHub Actions**.
- Confirm the latest **Publish user guide** run on `main` succeeded. Inspect the failed step in **Actions** if it did not.
- Use the deployment URL shown in the workflow run; allow a short propagation delay and refresh the browser.
- Check that `site/index.html` is present on `main`. The configured workflow uploads that directory, not `docs/`.

GitHub's current setup instructions: [configure a publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site) and [use a custom workflow](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
