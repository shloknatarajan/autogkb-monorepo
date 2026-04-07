# AutoGKB App

A React-based application for analyzing pharmacogenomic (PGx) research papers using Large Language Models (LLMs). This tool provides an interactive interface for viewing medical research data with markdown content and structured JSON annotations generated from the base [AutoGKB](https://github.com/DaneshjouLab/AutoGKB) repo

## Features

- **Study Dashboard**: Browse available medical research studies with search functionality
- **Interactive Viewer**: View research papers in markdown format alongside structured JSON annotations
- **Quote Highlighting**: Click on quotes in annotations to highlight corresponding text in the paper
- **Responsive Design**: Modern UI built with shadcn/ui components and Tailwind CSS
- **Data Management**: Automatically detects and loads studies from local data files

## Project Structure

```
src/
├── components/
│   ├── ui/                 # shadcn/ui components
│   └── viewer/             # Custom viewer components
├── hooks/                  # Custom React hooks
├── pages/                  # Main application pages
│   ├── Dashboard.tsx       # Study listing page
│   ├── Viewer.tsx          # Paper viewer page
│   └── NotFound.tsx        # 404 page
├── contexts/               # React contexts
└── lib/                    # Utility functions

public/data/
├── markdown/               # Research papers in markdown format
└── annotations/            # JSON annotations for papers
```

## Getting Started

### Prerequisites

- Node.js (v18 or higher)
- npm or yarn

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd autogkb-app
```

2. Install dependencies:
```bash
npm install
```

3. Start the development server:
```bash
npm run dev
```

The application will be available at `http://localhost:5173`

## Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm run build:dev` - Build for development
- `npm run lint` - Run ESLint
- `npm run preview` - Preview production build

## Adding New Studies

To add new research studies:

1. Convert an article PMCID to markdown using [PubMedDownloader](https://github.com/shloknatarajan/PubMedDownloader)
2. Generate new annotations using the base [AutoGKB](https://github.com/DaneshjouLab/AutoGKB) repo
3. Place the markdown file in `public/data/markdown/` (e.g., `PMC1234567.md`)
4. Place the corresponding JSON annotations in `public/data/annotations/` (e.g., `PMC1234567.json`)
5. The application will automatically detect and load the new study


## Tech Stack

- **Frontend**: React 18, TypeScript
- **Build Tool**: Vite
- **Styling**: Tailwind CSS
- **UI Components**: shadcn/ui, Radix UI
- **Routing**: React Router
- **State Management**: TanStack Query
- **Form Handling**: React Hook Form
- **Validation**: Zod
- **Icons**: Lucide React
