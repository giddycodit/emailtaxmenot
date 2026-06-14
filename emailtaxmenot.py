import sys
import getpass
from imapclient import IMAPClient

# ensure email argument is provided
if len(sys.argv) < 2:
  print("Missing email address argument.")
  print("Usage: python emailtaxmenot <youremail@domain.com>")
  sys.exit(1)

THE_EMAIL = sys.argv[1].lower()
BATCH_SIZE = 5000

if "yahoo.com" in THE_EMAIL:
  IMAP_SERVER = "://yahoo.com"
  TRASH_FOLDER = "Trash"
elif "gmail.com" in THE_EMAIL:
  IMAP_SERVER = "://gmail.com"
  TRASH_FOLDER = "[Gmail]/Trash"
else:
  print("Unsupported email domain. This script supports yahoo or gmail domains")
  sys.exit(1)

#securely capture email password via terminal without echoing text
APP_PASSWORD = getpass.getpass(prompt="Please generate the 16 character App Password generating from your email provider for emailtaxmenot")

print(f"Connecting to {IMAP_SERVER} ...")

try:
  with IMAPClient(IMAP_SERVER, use_uid=True) as server:
    server.login(THE_EMAIL, APP_PASSWORD)
    server.select_folder("INBOX")
    print(f"Executing global index search for 'unsubscribe'...")
    messages = server.serach(['TEXT', 'unsubscribe'])
    total_messages = len(messages)
    print(f"Total emails (aka 'pointers') found: {total_messages}")

    if total_messages == 0:
      print("No messages with unsubscribe found in Inbox")
      sys.exit(0)

      
    #batch transaction loops
    for i in range(0,total_messages, BATCH_SIZE):
      chunk = messages[i:i + BATCH_SIZE]
      server.move(chunk, TRASH_FOLDER)
      print(f"Moved batch {i} of {BATCH_SIZE} emails to Trash folder")
      
    print(f"\nEmails with 'unsubscribe' moved to Trash folder." \
      "You can now review them there. You can move any you do not want to delete into another folder." \
      "Then you can empty the Trash folder and lower your email storage!")
except Exception as e:
  print(f"\n uhoh! An exception occured: {e}")
