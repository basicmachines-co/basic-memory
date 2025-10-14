---
title: 'SPEC-4: Notes Web UI Component Architecture'
type: note
permalink: specs/spec-4-notes-web-ui-component-architecture
tags:
- frontend
- 'component-architecture'
- vue
- 'refactoring'
---

# SPEC-4: Notes Web UI Component Architecture

## Why

The current Notes.vue component is a monolithic component that handles multiple responsibilities, making it difficult to maintain, test, and understand. This leads to:

- Complex state management across multiple concerns
- Difficult to isolate and test individual features
- Hard to understand the full scope of functionality
- Circular refactoring cycles when making changes
- Poor separation of concerns between navigation, display, and interaction logic

We need to decompose this into focused, single-responsibility components that are easier to develop, test, and maintain while preserving the existing functionality users expect.

## What

This spec defines the component architecture for decomposing the Notes web UI into focused components with clear responsibilities and interactions.

**Affected Areas:**
- `/apps/web/components/notes/Notes.vue` - Will be decomposed into smaller components
- `/apps/web/components/notes/` - New component structure 
- Existing composables: `useNotesNavigation`, `useNotesFiltering`, `useNotesLayout`
- Mobile responsive behavior and layout management

**Component Breakdown:**

```
┌───────────────────────┬─────────────────────────────────────┬────────────────────────────────────────────────────────────┐
│ [Project]             │ [Project Name]            A/Z | ^   │ [edit | view]                                   [actions]  │
├───────────────────────┼─────────────────────────────────────┤                                                            │
│ All Notes             ├─────────────────────────────────────┼────────────────────────────────────────────────────────────┤
│ Recent                │     search...                       │  [note header]                                             │
│ [Project base dir]    ├─────────────────────────────────────┤                                                            │
│                       ├─────────────────────────────────────┤                                                            │
│ Folder1               │ Title                   [modified]  │                                                            │
│ Folder2               │                                     ├────────────────────────────────────────────────────────────┤
│ - Nested              │ snippet                             │  [note body]                                               │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       ├─────────────────────────────────────┤                                                            │
│                       ├─────────────────────────────────────┤                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       ├─────────────────────────────────────┤                                                            │
│                       ├─────────────────────────────────────┤                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       ├─────────────────────────────────────┤                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
│                       │                                     │                                                            │
└───────────────────────┴─────────────────────────────────────┴────────────────────────────────────────────────────────────┘
```


### ProjectSwitcher Component
- **Location**: Top-left dropdown
- **Responsibility**: Allow users to switch between Basic Memory projects
- **Behavior**: Selecting different project controls entire Notes page content
- **State**: When switching projects, reset to "All notes" view

### NotesNav Component  
- **Views**: Three mutually exclusive options:
  - **All notes**: Display all notes in project alphabetically
  - **Recent**: Display all notes in project by updated time (desc)
  - **Project**: Display notes in top-level directory of project
- **Interaction**: Only one view can be active at a time
- **Folder Integration**: All/Recent ignore folder selection; Project respects folder selection

### FolderTree Component
- **Display**: Nested list of all folders in project as tree view
- **Interaction**: Selecting folder filters notes in NotesList using directoryList API
- **Navigation Integration**: Selecting folder automatically switches NotesNav to "Project" view for clear UX
- **API Integration**: Uses directoryList API call via useDirectoryListQuery for folder-specific note fetching
- **State Coordination**: Folder selection coordinates with navigation state for intuitive user experience

### NotesList Component
- **Display**: Vertically scrolling cards showing note summaries
- **Information per card**:
  - Note title
  - Modified time (relative, e.g., "7 minutes ago")  
  - Short summary of note content (one line preview)
- **Behavior**: Updates based on NotesNav selection and FolderTree filtering

### NoteDetail Component
- **Display**: Full content of selected note
- **Sections**:
  - Header: Displays frontmatter information
  - Content: Note body content
- **Editing**: Current textarea implementation (rich editor in future spec)
- **Frontmatter**: Leave current implementation (enhancement in future spec)

## How (High Level)

### Component Architecture Approach
1. **Single Responsibility**: Each component handles one primary concern
2. **Clear Data Flow**: Props down, events up pattern for component communication
3. **Composable Integration**: Use existing composables for state management
4. **Progressive Decomposition**: Extract components incrementally to maintain functionality

### Implementation Strategy
1. **Extract ProjectSwitcher**: Move project switching logic to dedicated component
2. **Extract NotesNav**: Isolate navigation state and view selection logic
3. **Extract FolderTree**: Separate folder display and selection logic
4. **Extract NotesList**: Isolate note listing and card display logic  
5. **Extract NoteDetail**: Separate note content display and editing
6. **Update Notes.vue**: Become orchestration component managing component interactions

### State Management Integration
- **useNotesNavigation**: Manages navigation state (All/Recent/Project)
- **useNotesFiltering**: Handles filtering logic based on navigation and folder selection
- **useNotesLayout**: Manages responsive layout and panel visibility
- **Component State**: Each component manages its own internal UI state
- **Shared State**: Project selection and note filtering coordinated through composables

### Responsive Behavior

Mobile:
- Hide sidebar. pop out panel when selected 
- show note list on small screens (existing behavior)
- when note list item is clicked, display note detail on full page. Cancel or go back to return to list

Desktop: 
- Full three-column layout with all components visible

- **Transitions**: Smooth navigation between mobile panels

## How to Evaluate

### Success Criteria
- **Functional Parity**: All existing Notes page functionality preserved
- **Component Isolation**: Each component can be developed/tested independently  
- **Clear Responsibilities**: No overlapping concerns between components
- **State Clarity**: Clean data flow and state management patterns
- **Mobile Compatibility**: Responsive behavior maintains current UX
- **Performance**: No degradation in rendering or interaction performance

### Testing Procedure
1. **Functionality Validation**:
   - Project switching works correctly
   - All three navigation views (All/Recent/Project) function properly
   - Folder selection affects note display appropriately
   - Note selection and detail display works
   - Mobile responsive behavior preserved

2. **Component Isolation Testing**:
   - Each component can be imported and used independently
   - Component props and events are clearly defined
   - No tight coupling between components

3. **Integration Testing**:
   - Components communicate correctly through props/events
   - State management composables integrate properly
   - User workflows function end-to-end

4. **Performance Validation**:
   - Page load time unchanged or improved
   - Interaction responsiveness maintained
   - Memory usage stable or improved

### Implementation Validation
- **Code Review**: Clean component structure with single responsibilities
- **Type Safety**: Full TypeScript coverage with proper component prop types
- **Documentation**: Each component has clear interface documentation
- **Tests**: Unit tests for individual components and integration tests for workflows

## Observations

- [problem] Monolithic Notes.vue component creates maintenance and testing challenges #component-architecture
- [solution] Component decomposition improves separation of concerns and testability #refactoring
- [pattern] Progressive extraction maintains functionality while improving structure #incremental-improvement
- [interaction] NotesNav and FolderTree have conditional interaction based on selected view #state-management
- [constraint] Mobile responsive behavior must be preserved during decomposition #responsive-design
- [scope] Current editing and frontmatter capabilities remain unchanged #scope-limitation
- [validation] Functional parity is critical success criteria for this refactoring #validation-strategy
- [implementation] Folder selection now properly integrates with directoryList API for accurate filtering #api-integration
- [fix] FolderTree selection functionality completed - works across all navigation views #feature-complete
- [ux-improvement] FolderTree selection automatically switches NotesNav to Project view for clear user feedback #user-experience

## Relations

- depends_on [[SPEC-1: Specification-Driven Development Process]]
- implements [[Current Notes.vue functionality]]
- prepares_for [[Future rich editor spec]]
- prepares_for [[Future frontmatter editing spec]]
## Implementation Progress

### Components

1. **ProjectSwitcher** (`~/components/notes/ProjectSwitcher.vue`)
   - ✅ Top-left dropdown for project switching
   - ✅ Integrates with Pinia project store
   - ✅ Handles project switching with proper state reset
   - ✅ Responsive collapsed/expanded states
   - ✅ Expanded menu shows available projects and a Manage Projects option that navigates to the /settings/projects page
   - ✅ Simplified component following SortingToggle pattern - clean Props/Emits interface, uses ProjectItem type directly 

2. **NotesNav** (`~/components/notes/NotesNav.vue`)
   - ✅ Three mutually exclusive views: All/Recent/Project
   - ✅ Dynamic project title based on selected project
   - ✅ Clean props down, events up pattern
   - ✅ Responsive collapsed/expanded states with tooltips
   - ✅ The label for the Project selection should be the folder name for the project, not the project name

3. **FolderTree** (`~/components/notes/FolderTree.vue`)
   - ✅ Nested folder tree view for filtering
   - ✅ Uses `useFolderTree()` composable for data
   - ✅ Emits `folder-selected` events properly
   - ✅ Handles loading, error, and empty states
   - ✅ Includes companion `FolderTreeNode.vue` component
   - ✅ The current folder should be visibly selected in the tree  

4. **NotesList** (`~/components/notes/NotesList.vue`)
   - ✅ Vertically scrolling note summary cards
   - ✅ Shows title, updated time (relative), and content preview
   - ✅ Badge system for tags with variant logic
   - ✅ v-model integration for selectedNote
   - ✅ Smooth transitions and animations
   - ✅ Contextual title: The current folder name should be displayed at the top of the Notes list, or "All Notes", or "Recent" if they are selected
   - ✅ The title header should contain a toggle component to allow sorting with Lucide icon labels
     - sorting options:
       - name (asc/desc) - default 
       - file updated time (asc/desc)
     - If "Recent" notes nav option is selected the default order should be updated in descending order (recent first)

5. **NoteDisplay** (`~/components/notes/NoteDisplay.vue` - equivalent to spec's NoteDetail)
   - ✅ Full note content display
   - ✅ Edit/view mode toggle
   - ✅ Header with frontmatter information
   - ✅ Markdown rendering capabilities
   - ✅ Current textarea implementation preserved

### Architecture Requirements

1. **Component Isolation**: Each component can be developed/tested independently ✅
2. **Single Responsibility**: Each component handles one primary concern ✅
3. **Clear Data Flow**: Props down, events up pattern implemented ✅
4. **Composable Integration**: Uses existing composables for state management ✅
5. **Responsive Behavior**: Mobile/desktop layout preserved ✅

### State Management Integration

- **useNotesNavigation**: Manages navigation state (All/Recent/Project) ✅
- **useNotesFiltering**: Handles filtering logic based on navigation and folder selection ✅
- **useNotesLayout**: Manages responsive layout and panel visibility ✅
- **Component State**: Each component manages its own internal UI state ✅

### Interaction Logic

- Only one NotesNav view active at a time ✅
- All/Recent views ignore folder selection ✅ 
- Project view respects folder selection ✅
- Project switching resets to "All notes" view ✅

### TypeScript Coverage

- All components have full TypeScript coverage ✅
- Component props and events properly typed ✅
- No TypeScript errors in codebase ✅

### Success Criteria Validation

1. **Functional Parity**: All existing Notes page functionality preserved ✅
2. **Component Isolation**: Each component can be developed/tested independently ✅
3. **Clear Responsibilities**: No overlapping concerns between components ✅
4. **State Clarity**: Clean data flow and state management patterns ✅
5. **Mobile Compatibility**: Responsive behavior maintains current UX ✅
6. **Performance**: No degradation in rendering or interaction performance ✅

## Implementation Decisions

### Architectural Patterns

1. **Composition API + `<script setup>`**: All components use modern Vue 3 syntax
2. **Pinia Store Integration**: Project switching handled through reactive store
3. **Composable Pattern**: State management distributed across focused composables
4. **Event-Driven Communication**: Clean parent-child communication via events
5. **Responsive-First Design**: Mobile/desktop layouts handled natively

### Key Technical Choices

1. **Progressive Enhancement**: Mobile-first responsive design with desktop enhancements
2. **State Reset Logic**: Project switching properly resets navigation, search, and selection state
3. **Performance Optimizations**: Efficient re-rendering with proper key usage and transitions
4. **Accessibility**: Screen reader support, tooltips, keyboard navigation
5. **Type Safety**: Full TypeScript coverage with proper component prop definitions

### Quality Metrics

- **Code Maintainability**: High - each component is focused and independently testable
- **Performance**: Excellent - no performance degradation from decomposition  
- **User Experience**: Preserved - all existing functionality and responsive behavior maintained
- **Developer Experience**: Improved - cleaner component structure for future development
