import os
import sys
import getpass
import traceback
from imapclient import IMAPClient

ACCOUNT_CONFIG = {
  "yahoo.com": {
    "imap_server": "imap.mail.yahoo.com",
    "inbox": ["Inbox", "INBOX"],
    "trash": "Trash",
  },
  "gmail.com": {
    "imap_server": "imap.gmail.com",
    "inbox": ["INBOX", "Inbox"],
    "trash": "[Gmail]/Trash",
  },
}

BATCH_SIZE = 5000


def get_account_config(email):
  for domain, config in ACCOUNT_CONFIG.items():
    if domain in email:
      return config
  raise ValueError("Unsupported email domain. This script supports yahoo.com and gmail.com")


def prompt_app_password():
  secret = os.environ.get("YAHOO_APP_PASSWORD")
  if secret:
    return secret
  return getpass.getpass(
    prompt="Please enter the 16 character App Password that you from your email provider for app emailtaxmenot: "
  )


def login_to_imap(email, imap_server):
  print(f"Connecting to {imap_server} ...")
  server = IMAPClient(imap_server, use_uid=True, ssl=True)
  app_password = prompt_app_password()
  if not app_password:
    raise ValueError("No app password provided via YAHOO_APP_PASSWORD or prompt.")
  server.login(email, app_password)
  return server


def select_inbox_folder(server, inbox_candidates):
  for candidate in inbox_candidates:
    try:
      server.select_folder(candidate)
      return candidate
    except Exception:
      continue
  raise RuntimeError(
    f"Unable to select the Inbox folder with any of: {', '.join(inbox_candidates)}"
  )


def move_unsubscribe_emails_to_trash(server, inbox_candidates, trash_folder, limit=10000):
  selected_folder = select_inbox_folder(server, inbox_candidates)
  print(f"Selected folder: {selected_folder}")
  print("Executing global index search for 'unsubscribe'...")

  messages = find_unsubscribe_message_ids(server)
  total_messages = len(messages)
  print(f"Total emails (aka 'pointers') found: {total_messages}")

  if total_messages == 0:
    print("No messages with unsubscribe found in Inbox")
    return

  if limit is not None and total_messages > limit:
    messages = messages[:limit]
    total_messages = len(messages)
    print(f"Limiting movement to the first {limit} messages.")

  for i in range(0, total_messages, BATCH_SIZE):
    chunk = messages[i:i + BATCH_SIZE]
    server.move(chunk, trash_folder)
    print(f"Moved batch {i} of {BATCH_SIZE} emails to {trash_folder}")

  print(
    f"\nEmails with 'unsubscribe' moved to {trash_folder}. "
    "You can now review them there. You can move any you do not want to delete into another folder. "
    "Then you can empty the Trash folder and lower your email storage!"
  )

  # show current trash count after moving so user can decide about emptying
  trash_count = get_folder_count(server, trash_folder)
  if trash_count is None:
    print(f"Unable to determine message count for Trash folder '{trash_folder}'.")
  else:
    print(f"Current messages in {trash_folder}: {trash_count}")


def preview_unsubscribe_emails(server, inbox_candidates):
  selected_folder = select_inbox_folder(server, inbox_candidates)
  print(f"Selected folder: {selected_folder}")
  print("Executing preview search for 'unsubscribe'...")

  messages = find_unsubscribe_message_ids(server)
  total_messages = len(messages)
  print(f"Total matching messages in Inbox: {total_messages}")

  if total_messages == 0:
    print("No messages with unsubscribe found in Inbox.")


def find_unsubscribe_message_ids(server):
  return search_in_uid_ranges(server, ["TEXT", "unsubscribe"])


def search_in_uid_ranges(server, search_criteria, chunk_size=5000, max_empty=3):
  messages = []
  start = 1
  empty_runs = 0

  while empty_runs < max_empty:
    end = start + chunk_size - 1
    query = ["UID", f"{start}:{end}"] + search_criteria
    try:
      chunk = server.search(query)
    except Exception:
      break

    if chunk:
      messages.extend(chunk)
      empty_runs = 0
    else:
      empty_runs += 1

    start = end + 1
    if start > 1000000:
      break

  return sorted(set(messages))


def get_folder_count(server, folder_name):
  try:
    server.select_folder(folder_name)
    status = server.folder_status(folder_name, ["MESSAGES"])
    return int(status.get("MESSAGES", 0))
  except Exception:
    return None


def preview_unsubscribe_emails_with_trash(server, inbox_candidates, trash_folder):
  # select inbox and show total inbox count
  selected_folder = select_inbox_folder(server, inbox_candidates)
  print(f"Selected folder: {selected_folder}")

  inbox_total = get_folder_count(server, selected_folder)
  if inbox_total is None:
    print(f"Unable to determine total messages in Inbox folder '{selected_folder}'.")
  else:
    print(f"Total messages in {selected_folder}: {inbox_total}")

  # show matching messages in Inbox using the broader unsubscribe search
  print("Executing preview search for 'unsubscribe'...")
  messages = find_unsubscribe_message_ids(server)
  match_total = len(messages)
  print(f"Total matching messages in Inbox: {match_total}")
  if match_total == 0:
    print("No messages with unsubscribe found in Inbox.")

  # show current Trash count (heads-up)
  trash_count = get_folder_count(server, trash_folder)
  if trash_count is None:
    print(f"Unable to determine message count for Trash folder '{trash_folder}'.")
  else:
    print(f"Current messages in {trash_folder}: {trash_count}")


def summarize_folders(server):
  print("Fetching folder list...")
  folders = server.list_folders()
  if not folders:
    print("No folders found.")
    return

  print(f"Found {len(folders)} folders. Scanning each folder...")
  for flags, delimiter, folder_name in folders:
    try:
      server.select_folder(folder_name, readonly=True)
      status = server.folder_status(folder_name, ["MESSAGES", "UNSEEN"])
      total = int(status.get("MESSAGES", 0))
      unread = int(status.get("UNSEEN", 0))
      unsubscribe_count = len(search_in_uid_ranges(server, ["TEXT", "unsubscribe"]))
      print(
        f"Folder: {folder_name} | Total: {total} | Unread: {unread} | Unsubscribe: {unsubscribe_count}"
      )
    except Exception as e:
      print(f"Skipping {folder_name}: {e}")


def empty_trash_folder(server, trash_folder):
  print("WARNING: Emptying Trash will permanently delete all messages in the Trash folder.")
  confirmation = input("Type 'Yes' to proceed: ")
  if confirmation != "Yes":
    print("Aborting empty action.")
    return

  server.select_folder(trash_folder)
  messages = server.search(["ALL"])
  total_messages = len(messages)
  print(f"Total messages in {trash_folder}: {total_messages}")

  if total_messages == 0:
    print("Trash folder is already empty.")
    return

  for i in range(0, total_messages, BATCH_SIZE):
    chunk = messages[i:i + BATCH_SIZE]
    server.delete_messages(chunk)
  server.expunge()

  print(f"Trash folder {trash_folder} has been emptied permanently.")


def print_usage():
  print("Usage: python emailtaxmenot.py <youremail@domain.com> <action> [limit]")
  print("Actions:")
  print("  preview - count 'unsubscribe' messages in Inbox without moving anything")
  print("  clean   - move 'unsubscribe' emails from Inbox to Trash")
  print("            optional [limit] sets the maximum number of messages to move")
  print("  empty   - permanently delete all messages in Trash")
  print("  summary - list all folders with total, unread, and unsubscribe counts")


def main():
  if len(sys.argv) < 3:
    print("Missing email address or action.")
    print_usage()
    sys.exit(1)

  email = sys.argv[1].lower()
  action = sys.argv[2].lower()

  if action not in {"preview", "clean", "empty"}:
    print(f"Unknown action: {action}")
    print_usage()
    sys.exit(1)

  try:
    config = get_account_config(email)
  except ValueError as error:
    print(error)
    sys.exit(1)

  limit = 10000
  if action == "clean":
    if len(sys.argv) >= 4:
      try:
        limit = int(sys.argv[3])
      except ValueError:
        print("Invalid limit. Limit must be a number.")
        print_usage()
        sys.exit(1)
  elif len(sys.argv) >= 4:
    print("The preview and empty actions do not accept a limit argument.")
    print_usage()
    sys.exit(1)

  try:
    with login_to_imap(email, config["imap_server"]) as server:
      if action == "preview":
        preview_unsubscribe_emails_with_trash(server, config["inbox"], config["trash"])
      elif action == "clean":
        move_unsubscribe_emails_to_trash(
          server,
          config["inbox"],
          config["trash"],
          limit,
        )
      elif action == "summary":
        summarize_folders(server)
      else:
        empty_trash_folder(server, config["trash"])
  except Exception as error:
    print(f"\nUhoh! An exception occurred: {error}")
    traceback.print_exc()
    sys.exit(1)


if __name__ == "__main__":
  main()
