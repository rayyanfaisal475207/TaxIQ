$files = git ls-files --others --exclude-standard
git config user.name "rayyanfaisal475207"
git config user.email "rayyanfaisal475207@users.noreply.github.com"
git branch -M main

foreach ($f in $files) {
    if ($f -match 'frontend/') {
        $desc = "Implement React frontend component and styles"
    } elseif ($f -match 'admin-frontend/') {
        $desc = "Implement admin dashboard interface and monitoring"
    } elseif ($f -match 'src/api/') {
        $desc = "Add FastAPI endpoint and routing logic"
    } elseif ($f -match 'src/database/' -or $f -match 'alembic/') {
        $desc = "Implement database models, schemas, and migrations"
    } elseif ($f -match 'src/pipeline/' -or $f -match 'src/ingestion/') {
        $desc = "Build RAG pipeline and document ingestion modules"
    } elseif ($f -match 'src/retrieval/' -or $f -match 'src/llm/') {
        $desc = "Add vector retrieval and LLM integration services"
    } elseif ($f -match 'docs/' -or $f -match 'README.md' -or $f -match 'SUPABASE_SETUP.md') {
        $desc = "Add comprehensive project documentation and setup guides"
    } elseif ($f -match 'tests/') {
        $desc = "Add automated test coverage"
    } elseif ($f -match 'scripts/') {
        $desc = "Add utility script for project maintenance"
    } elseif ($f -match '.github/') {
        $desc = "Configure GitHub Actions CI/CD pipeline"
    } else {
        $desc = "Add core configuration and dependency definitions"
    }
    
    $filename = Split-Path $f -Leaf
    git add "$f"
    git commit -m "Add $filename" -m "$desc for $f to maintain modularity."
}
