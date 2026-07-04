# Workflow Templates

Copy these files to `.github/workflows/` after refreshing GitHub CLI auth:

```bash
gh auth refresh -h github.com -s workflow
mkdir -p .github/workflows
cp docs/workflows/*.yml .github/workflows/
git add .github/workflows
git commit -m "add ci workflows"
git push
```

The templates are parked here because GitHub rejects pushes that create active workflow files unless the token has `workflow` scope.
