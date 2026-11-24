# Messaging rebuild hub

## Tasks

| ID | Task | Slice | Status | Depends on | Est dev h | Actual h | Requirements | PR |
|----|------|-------|--------|------------|-----------|----------|--------------|----|
| T001 | Conversation list API | A | ✅ Done | — | 6 | 7 | MSG-01 | #12 |
| T002 | Conversation list screen | A | ✅ Done | T001 | 8 | 9 | MSG-01, MSG-02 | #14 |
| T003 | Unread badge | A | Done | T002 | 6 | 4 | MSG-03 | [#15](https://github.com/example/messaging-app/pull/15) |
| T004 | Message thread API | B | In progress | T001 | 8 | 3 | MSG-04 | |
| T005 | Message thread screen | B | Todo | T004, T002 | 10 | | MSG-04, MSG-05 | |
| T006 | Send message | B | Todo | T005 | 6 | | MSG-06 | |
| T007 | Attachments upload API | C | Todo | T004 | 8 | | MSG-07 | |
| T008 | Attachment picker | C | Todo | T007, T006 | 8 | | MSG-07 | |
| T009 | Profile details API | D1 | Todo | | 5 | | PD-01 | |
| T010 | Profile details screen | D1 | Todo | T009 | 8 | | PD-01, PD-02 | |
| T011 | Edit profile | D2 | Blocked | T010 | 8 | | PD-03 | |
| T012 | Push notifications | E1 | Todo | T006 | 12 | | MSG-08 | |

## Test cases

| TC | Scenario | Task | Kind | Result | Bug |
|----|----------|------|------|--------|-----|
| TC-001 | List returns newest first | T001 | API | Pass | |
| TC-002 | List paginates at 50 | T001 | API | Pass | |
| TC-003 | Empty state shows illustration | T002 | UI | Pass with question: is the copy final? | |
| TC-004 | Pull to refresh | T002 | UI | Pass | |
| TC-005 | Long names truncate | T002 | UI | Pass (re-tested after BUG-001) | BUG-001 |
| TC-006 | Badge clears on open | T003 | UI | Pass | |
| TC-007 | Badge caps at 99+ | T003 | UI | Fail | BUG-002 |
| TC-008 | Thread loads 30 messages | T004 | API | Blocked | |
| TC-009 | Thread 404 for other user | T004 | API | Not run | |

## Defects

| ID | Defect | Severity | Status | Task | Test case | Fix commit |
|----|--------|----------|--------|------|-----------|------------|
| BUG-001 | Long names overflow the row | Major | Fixed | T002 | TC-005 | a1b2c3d |
| BUG-002 | Badge shows 100 instead of 99+ | Minor | Open | T003 | TC-007 | |

## Decisions

| ID | Decision | Owner | Status |
|----|----------|-------|--------|
| DEC-01 | Use cursor pagination for threads | Milan | Decided |
| DEC-02 | Attachments limited to 25 MB | Priya | Pending |

## Open questions

| ID | Question | Owner | Waiting on |
|----|----------|-------|------------|
| Q-01 | Is the empty-state copy final? | Milan | Design (Anita) |
| Q-02 | Do we need read receipts in v1? | Milan | Client |

## Requirements

| Req | Requirement |
|-----|-------------|
| MSG-01 | User sees a list of conversations |
| MSG-02 | List refreshes on pull |
| MSG-03 | Unread count is visible |
| MSG-04 | User can open a thread |
| MSG-05 | Thread shows timestamps |
| MSG-06 | User can send a text message |
| MSG-07 | User can attach a file |
| MSG-08 | User gets a push for new messages |
| MSG-09 | User can mute a conversation |
| PD-01 | User can view profile details |
| PD-02 | Profile shows avatar |
| PD-03 | User can edit profile |
