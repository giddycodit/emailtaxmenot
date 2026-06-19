from http import server
import os
from pyexpat.errors import messages
import sys
import getpass
import traceback
from imapclient import IMAPClient
import datetime
import time


ACCOUNT_CONFIG = {
  "yahoo.com": {
    "imap_server_read": "export.imap.mail.yahoo.com",
    "imap_server_write": "imap.mail.yahoo.com",
    "imap_port_read": 993,
    "imap_port_write": 993,
    "inbox": ["Inbox", "INBOX"],
    "trash": "Trash",
  },
  "gmail.com": {
    "imap_server_read": "imap.gmail.com",
    "imap_server_write": "imap.gmail.com",
    "imap_port_read": 993,
    "imap_port_write": 993,
    "inbox": ["INBOX", "Inbox"],
    "trash": "[Gmail]/Trash",
  },
}
server = None
server_write = None

# Safety Adjustments
CHUNK_SIZE = 500  # Number of emails moved per batch
PAUSE_BETWEEN_CHUNKS = 1.5  # Seconds to rest between batches to prevent bans


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
    prompt="Please enter the 16 character App Password that you generated in your email provider for app emailtaxmenot: "
  )


def login_to_imap(email, imap_server, imap_port):
  print(f"Connecting to {imap_server} ...")
  server = IMAPClient(imap_server, port=imap_port, use_uid=True, ssl=True)
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


# Safety Adjustments (Ensure these are defined globally in your script)
CHUNK_SIZE = 500
PAUSE_BETWEEN_CHUNKS = 1.5

def get_folder_status(server, to_folder):
  if not server.folder_exists(to_folder):
      print(f"get_folder_status: Folder '{to_folder}' does not exist. Returning ...")
      return -1
          
# Inline status check for the Trash folder count
  try:
      to_folder_status = server.folder_status(to_folder, ["MESSAGES"])
      to_folder_count = to_folder_status[b"MESSAGES"]
        # Handle library variance for byte vs string key dictionary mapping
      to_folder_count = to_folder_status.get(b"MESSAGES") or to_folder_status.get("MESSAGES", 0)
      #if you select the folder you can also get count using - total_emails = folder_info['EXISTS'] 
      print(f"get_folder_status: Current messages in {to_folder}: {to_folder_count:,}")
      return to_folder_count  
  
  except Exception:
      print(f"Unable to determine message count for  folder '{to_folder}'.")
      return -1
   
def create_folder_if_not_exists(server, folder_name): 
    try:
        if server.folder_exists(folder_name):
            print(f"Folder '{folder_name}' already exists.")
            return
        
        server.create_folder(folder_name)
        print(f"Folder '{folder_name}' created successfully.")
    except Exception as e:
        print(f"Error occurred while creating folder '{folder_name}': {e}")


def move_messages_to_folder(server, messages, from_folder, to_folder, create_folder=True, chunk_size=CHUNK_SIZE, prompt_user=True, limit=None):
  
  print(f"\nmove_messages_to_folder: {from_folder} -> {to_folder}.")
  
  if create_folder:
    create_folder_if_not_exists(server, to_folder)

  total_messages = len(messages)
  
  # Convert to a set to remove duplicates, then back to a sorted list
  unique_messages = sorted(list(set(messages)))

  print(f"Messages to move: {len(messages):,}")
  messages = unique_messages

  total_messages = len(messages)
  print(f"Total unique messages to move: {total_messages:,}")

  if total_messages == 0:
      print("No messages to move. Returning")
      return
      
  # 3. Apply the user-defined safety cap limit if needed
  if limit is not None and total_messages > limit:
      messages = messages[:limit]
      total_messages = len(messages)
      print(f"Limiting movement execution to the first {limit:,} messages.")


  # ==================== YOUR USER INTERACTIVE PROMPT ====================
  if prompt_user:
    print(f"\n⚠️ WARNING: You are about to move {total_messages:,} emails to the folder'{to_folder}. You can review them there. " )
    user_confirmation = input("Are you sure you want to proceed? Type 'Yes' to continue: ")
    
    if user_confirmation.strip() != "Yes":
        print("Operation cancelled by user. No emails were moved.")
        return
  # =======================================================================
  to_folder_count = get_folder_status(server, to_folder)

  # 4. Chunked movement loop with the 1.5-second anti-ban pause
  print(f"\nStarting chunked migration to {to_folder}...")
  for i in range(0, total_messages, chunk_size):
      chunk = messages[i:i + chunk_size]
      
      # Atomically copy to Trash and mark original as deleted
      server_write.move(chunk, to_folder)
      print(f"Moved batch starting at index {i:,} ({len(chunk)} emails) to {to_folder}")
      
      # Critical safety pause to stay under Yahoo's command-frequency firewall thresholds
      time.sleep(PAUSE_BETWEEN_CHUNKS)
      
  print(
      f"\nEmails moved to folder {to_folder}. "
      "You can now review them there.  "     
  )
  
  server_write.noop()  # Refresh server state after moving messages
  server.noop()  # Refresh server state after moving messages

  # 5. Inline status check for the Trash folder count
  try:
      to_folder_count = get_folder_status(server, to_folder)
      if to_folder_count < total_messages:
        print("ℹ️ Note: Yahoo server-side indexing can lag causing the count to be inaccurate.")
        print("The emails are safe; log into mail.yahoo.com to see the fully updated tally.")

  except Exception:
      print(f"Unable to determine message count for  folder '{to_folder}'.")



def move_all_folder_messages(server, from_folder, to_folder, chunk_size=500):
    """
    Fetches all UIDs once to prevent Yahoo index fragmentation bugs, 
    then moves them in cautious batches with error retries.
    """
    # 1. Select the source folder
    try:

        print(f"Opened '{from_folder}'. ")
        total_messages = get_folder_status(server, from_folder)
        print(f"Total messages in '{from_folder}': {total_messages}")   
        if total_messages == 0:
            print("Folder is empty.")
            return
        server.select_folder(from_folder, readonly=False)

    except Exception as e:
        print(f"Error selecting source folder '{from_folder}': {e}")
        return

    # 2. Fetch ALL static UIDs right now so we don't rely on shifting indexes
    print("Fetching absolute UID list from Yahoo... (This stays stable)")
    all_uids = server.search(['ALL'])
    total_emails = len(all_uids)
    print(f"Successfully indexed {total_emails} messages to move.")

    if total_emails == 0:
        print("Folder is empty.")
        return 0

    total_moved = 0

    # 3. Slice the stable Python list of UIDs into blocks
    for i in range(0, total_emails, chunk_size):
        chunk = all_uids[i : i + chunk_size]
        
        # Retry loop specifically designed to beat Yahoo's [SERVERBUG]
        retries = 3
        while retries > 0:
            try:
                print(f"Moving chunk {i//chunk_size + 1} ({len(chunk)} emails)...")
                
                # Move this explicit batch of permanent IDs
                server.move(chunk, to_folder)
                
                total_moved += len(chunk)
                
                # CRITICAL: Cooldown gives Yahoo's database time to re-index rows
                time.sleep(1.5) 
                break # Success, exit retry loop
                
            except Exception as e:
                retries -= 1
                error_str = str(e)
                print(f" -> Yahoo Server Hitch: {error_str}")
                
                if "SERVERBUG" in error_str or "try again later" in error_str:
                    print(f" -> Pausing 5 seconds to let Yahoo recover. Retries left: {retries}")
                    time.sleep(5)
                else:
                    print(" -> Non-recoverable error. Stopping.")
                    return

        if retries == 0:
            print("\n[Fatal] Yahoo refused to clear this chunk after multiple retries. Stopping.")
            return

    print(f"\nProcessing complete! Successfully migrated {total_moved} emails to '{to_folder}'.")
    return total_moved


def move_unsubscribe_emails_to_trash2(server, inbox_candidates, trash_folder, limit=10000):
    # 1. Dynamically find and select the Inbox from candidates
    selected_folder = None
    for folder in inbox_candidates:
        if server.folder_exists(folder):
            selected_folder = folder
            break
            
    if not selected_folder:
        print("Error: Could not find a valid Inbox folder from candidates.")
        return 0

    # Select the inbox in read/write mode so we can move items out of it
    server.select_folder(selected_folder, readonly=False)
    print(f"Selected folder: {selected_folder}")
    
    # 2. Execute a universal, year-by-year search with intra-year pagination
    print("Executing historical index search for 'unsubscribe'...")
    messages = []

    # Dynamically get the current year and stop at Yahoo Mail's birth year
    current_year = datetime.datetime.now().year
    FIRST_POSSIBLE_YEAR = 1997 
    
    # Track the absolute lowest UID found across the entire run to use as an upper boundary
    lowest_uid_marker = None
    year_marker = None
    currentYearCount = 0
    # Loops backward from the current year all the way down to 1997
    for year in range(current_year, FIRST_POSSIBLE_YEAR - 1, -1):
    #for year in [FIRST_POSSIBLE_YEAR]:
        print(f"Scanning year {year} for matching emails...")
        
        # Internal control flags to paginate years containing more than 1,000 matches
        year_complete = False
        #year_marker = None #lowest_uid_marker
        currentYearCount = 0
        year_marker = None
        last_current_max = None
        
        while not year_complete:
            since_date = f"01-Jan-{year}"
            before_date = f"01-Jan-{year + 1}"

            # Explicitly quote '"unsubscribe"' so Yahoo parses the search text properly
            search_criteria = ['BODY', '"unsubscribe"','SINCE', since_date  ]
            #search_criteria = ['BODY', '"unsubscribe"']
            
            
            try:
                doneWithYear = False
                while not doneWithYear:
                  search_criteria = ['BODY', '"unsubscribe"','SINCE', since_date  ]
                  # Only inject the ceiling restriction if we have a valid, active marker
                  if year_marker is not None:
                      # Instead, define sliding chunk boundaries dynamically inside your loop loop:
                      #search_low = year_marker - 5000
                      uid_search_range = f"1:{year_marker-1}"
                      #uid_search_range = f"{current_max + 1}:{current_max+500}"
                      last_current_max = current_max
                      #print(f"Scanning UID range: {uid_search_range}")
                      #search_criteria.extend(['UID', uid_search_range])
                      #search_criteria = f"['UID', '{uid_search_range}']"  

                  

                  print(f"Searching with criteria: {search_criteria} ")

                    #yearly_matches = server.search(search_criteria)        
                  # This forces Yahoo to scan only one narrow window block at a time
                  yearly_matches = server.search(search_criteria)
                  
                  if isinstance(yearly_matches, list) and yearly_matches and len(yearly_matches) > 0:
                      messages.extend(yearly_matches)
                      count_found = len(yearly_matches)
                      currentYearCount += count_found



                      # Update local tracking variables with the smallest tracking pointer found
                      current_min = min(yearly_matches)
                      current_max = max(yearly_matches)
                      year_marker = current_min
                      lowest_uid_marker = current_min
                      
                      print(f"returned results: found: {count_found},  current_min: {current_min}, current_max: {current_max},  lowest_uid_marker: {lowest_uid_marker}, since year: {year}  ")
                      #if year_marker:
                      print(f" Updated year_marker value: {year_marker}")

                      if last_current_max == current_max:
                          print(f"Warning: UID pagination appears to be stuck at {current_max}. Ending year scan to prevent infinite loop.")
                          doneWithYear = True

                  else:
                      #if year_marker == lowest_uid_marker:
                      if currentYearCount == 0:
                          print(f" -> No matches found in year {year}")
                      doneWithYear = True

                  if doneWithYear:
                      year_complete = True
                      print(f" -> Found {currentYearCount} final matches for year {year}")
                      
                      
                  # Quick 0.5-second rest between operations to keep the connection rock stable
                  time.sleep(0.5)
                
                  move_messages_to_folder(server, messages, "Inbox", "Pre Trash", True, CHUNK_SIZE, False , 2000)
                  # Close out the current folder view session to kill stale server memory pointers
                  server.close_folder()
                  server.noop()
                  server.select_folder(selected_folder, readonly=False)
                  total_messages += len(messages)
                  messages = []
                  
            except Exception as e:
                print(f"Warning: Scan interrupted at year {year}. Error: {e}")
                year_complete = True
                break

    
    print(f"\nSearch complete. Total 'unsubscribe' emails found across all years: {total_messages:,}")
    
    return total_messages
        
    #move_messages_to_folder(server, messages, "Inbox", "Pre Trash", True, CHUNK_SIZE, True, 2000)


def move_unsubscribe_emails_to_trash2_1(server, inbox_candidates, trash_folder, limit=10000):
    # 1. Dynamically find and select the Inbox from candidates
    selected_folder = None
    for folder in inbox_candidates:
        if server.folder_exists(folder):
            selected_folder = folder
            break
            
    if not selected_folder:
        print("Error: Could not find a valid Inbox folder from candidates.")
        return

    # Select the inbox in read/write mode so we can move items out of it
    server.select_folder(selected_folder, readonly=False)
    print(f"Selected folder: {selected_folder}")
    
    # 2. Execute a universal, year-by-year search with intra-year pagination
    print("Executing historical index search for 'unsubscribe'...")
    messages = []

    # Dynamically get the current year and stop at Yahoo Mail's birth year
    current_year = datetime.datetime.now().year
    FIRST_POSSIBLE_YEAR = 1997 
    
    # Track the absolute lowest UID found across the entire run to use as an upper boundary
    lowest_uid_marker = None
    year_marker = None
    currentYearCount = 0
    # Loops backward from the current year all the way down to 1997
    for year in range(current_year, FIRST_POSSIBLE_YEAR - 1, -1):
        print(f"Scanning year {year} for matching emails...")
        
        # Internal control flags to paginate years containing more than 1,000 matches
        year_complete = False
        #year_marker = None #lowest_uid_marker
        currentYearCount = 0
        year_marker = None
        
        while not year_complete:
            since_date = f"01-Jan-{year}"
            before_date = f"01-Jan-{year + 1}"

            # Explicitly quote '"unsubscribe"' so Yahoo parses the search text properly
            search_criteria = ['BODY', '"unsubscribe"','SINCE', since_date  ]
            
            
            
            try:
                doneWithYear = False
                while not doneWithYear:
                  # Only inject the ceiling restriction if we have a valid, active marker
                  if year_marker is not None:
                      # Instead, define sliding chunk boundaries dynamically inside your loop loop:
                      search_low = year_marker - 5000
                      uid_search_range = f"1:{year_marker-1}"
                      print(f"Scanning UID range: {uid_search_range}")
                      search_criteria.extend(['UID', uid_search_range])
        
                  # This forces Yahoo to scan only one narrow window block at a time
                  yearly_matches = server.search(search_criteria)
                  
                  if isinstance(yearly_matches, list) and yearly_matches:
                      messages.extend(yearly_matches)
                      count_found = len(yearly_matches)
                      currentYearCount += count_found



                      # Update local tracking variables with the smallest tracking pointer found
                      current_min = min(yearly_matches)
                      current_max = max(yearly_matches)
                      year_marker = current_min
                      lowest_uid_marker = current_min
                      
                      print(f"returned results: found: {count_found},  current_min: {current_min}, current_max: {current_max},  lowest_uid_marker: {lowest_uid_marker}, year: {year}  ")
                      #if year_marker:
                      print(f" Updated year_marker value: {year_marker}")


                      
                      # CRITICAL DETECTION: If it's exactly 1,000, Yahoo hit its response limit.
                      # Loop again within the same year using our updated year_marker ceiling.
                      #if count_found == 1000:
                      #    print(f" -> Found 1,000 matches (Limit hit. Paging deeper into year {year}...)")
                      #    time.sleep(0.3)  # Micro-break to keep connections stable
                      #else:``
                      #    print(f" -> Found {count_found:,} final matches for year {year}")
                      #    year_complete = True
                  else:
                      #if year_marker == lowest_uid_marker:
                      if currentYearCount == 0:
                          print(f" -> No matches found in year {year}")
                      doneWithYear = True

                  if doneWithYear:
                      year_complete = True
                      print(f" -> Found {currentYearCount} final matches for year {year}")
                      
                      
                  # Quick 0.5-second rest between operations to keep the connection rock stable
                  time.sleep(0.5)
                
            except Exception as e:
                print(f"Warning: Scan interrupted at year {year}. Error: {e}")
                year_complete = True
                break

    total_messages = len(messages)
    print(f"\nSearch complete. Total 'unsubscribe' emails found across all years: {total_messages:,}")
    
    if total_messages == 0:
        print("No messages with unsubscribe found in Inbox.")
        return
        
    # 3. Apply the user-defined safety cap limit if needed
    if limit is not None and total_messages > limit:
        messages = messages[:limit]
        total_messages = len(messages)
        print(f"Limiting movement execution to the first {limit:,} messages.")

    # ==================== YOUR USER INTERACTIVE PROMPT ====================
    print(f"\n⚠️ WARNING: You are about to move {total_messages:,} emails to the '{trash_folder}. You can review them there. " \
            f"\nKeep in mind that the Trash may be automatically permanently deleted by your email provider periodically.' folder.")
    user_confirmation = input("Are you sure you want to proceed? Type 'Yes' to continue: ")
    
    if user_confirmation.strip() != "Yes":
        print("Operation cancelled by user. No emails were moved.")
        return
    # =======================================================================
    #move_messages_to_folder(server, messages, "Inbox", to_folder, create_folder=True, chunk_size=1000, prompt_user=False, limit=limit):

    # 4. Chunked movement loop with the 1.5-second anti-ban pause
    print(f"\nStarting chunked migration to {trash_folder}...")
    for i in range(0, total_messages, CHUNK_SIZE):
        chunk = messages[i:i + CHUNK_SIZE]
        
        # Atomically copy to Trash and mark original as deleted
        server.move(chunk, trash_folder)
        print(f"Moved batch starting at index {i:,} ({len(chunk)} emails) to {trash_folder}")
        
        # Critical safety pause to stay under Yahoo's command-frequency firewall thresholds
        time.sleep(PAUSE_BETWEEN_CHUNKS)
        
    print(
        f"\nEmails with 'unsubscribe' moved to {trash_folder}. "
        "You can now review them there. You can move any you do not want to delete into another folder. "
        "Then you can empty the Trash folder and lower your email storage!"
    )
    
    # 5. Inline status check for the Trash folder count
    try:
        trash_status = server.folder_status(trash_folder, ["MESSAGES"])
        trash_count = trash_status[b"MESSAGES"]
        print(f"Current messages in {trash_folder}: {trash_count:,}")
    except Exception:
        print(f"Unable to determine message count for Trash folder '{trash_folder}'.")


def move_unsubscribe_emails_to_trash1(server, inbox_candidates, trash_folder, limit=10000):
    # 1. Dynamically find and select the Inbox from candidates
    selected_folder = None
    for folder in inbox_candidates:
        if server.folder_exists(folder):
            selected_folder = folder
            break
            
    if not selected_folder:
        print("Error: Could not find a valid Inbox folder from candidates.")
        return

    # Select the inbox in read/write mode so we can move items out of it
    server.select_folder(selected_folder, readonly=False)
    print(f"Selected folder: {selected_folder}")
    
    # 2. Execute a universal, year-by-year search to prevent Yahoo server timeouts
    print("Executing historical index search for 'unsubscribe'...")
    messages = []

    # Dynamically get the current year and stop at Yahoo Mail's birth year
    current_year = datetime.datetime.now().year
    FIRST_POSSIBLE_YEAR = 1997 
    
    # Loops backward from the current year all the way down to 1997
    for year in range(current_year, FIRST_POSSIBLE_YEAR - 1, -1):
        print(f"Scanning year {year} for matching emails...")
        
        # Formulate IMAP standard date boundaries for the calendar year
        since_date = f"01-Jan-{year}"
        #before_date = f"31-Dec-{year}"
        
        try:
            # This forces Yahoo to scan only one narrow year block at a time
            yearly_matches = server.search([
                'TEXT', 'unsubscribe',
                'SINCE', since_date
            ])
            
            if yearly_matches:
                messages.extend(yearly_matches)
                print(f" -> Found {len(yearly_matches):,} matches in year {year}")
            else:
                print(f" -> No matches found in year {year}")
                
            # Quick 0.5-second rest between year searches to keep the connection rock stable
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Warning: Scan interrupted at year {year}. Error: {e}")
            break

    total_messages = len(messages)
    print(f"\nSearch complete. Total 'unsubscribe' emails found across all years: {total_messages:,}")
    
    if total_messages == 0:
        print("No messages with unsubscribe found in Inbox.")
        return
        
    # 3. Apply the user-defined safety cap limit if needed
    if limit is not None and total_messages > limit:
        messages = messages[:limit]
        total_messages = len(messages)
        print(f"Limiting movement execution to the first {limit:,} messages.")
        
    # 4. Chunked movement loop with the 1.5-second anti-ban pause
    print(f"\nStarting chunked migration to {trash_folder}...")
    for i in range(0, total_messages, CHUNK_SIZE):
        chunk = messages[i:i + CHUNK_SIZE]
        
        # Atomically copy to Trash and mark original as deleted
        server.move(chunk, trash_folder)
        print(f"Moved batch starting at index {i:,} ({len(chunk)} emails) to {trash_folder}")
        
        # Critical safety pause to stay under Yahoo's command-frequency firewall thresholds
        time.sleep(PAUSE_BETWEEN_CHUNKS)
        
    print(
        f"\nEmails with 'unsubscribe' moved to {trash_folder}. "
        "You can now review them there. You can move any you do not want to delete into another folder. "
        "Then you can empty the Trash folder and lower your email storage!"
    )
    
    # 5. Inline status check for the Trash folder count
    try:
        trash_status = server.folder_status(trash_folder, ["MESSAGES"])
        trash_count = trash_status[b"MESSAGES"]
        print(f"Current messages in {trash_folder}: {trash_count:,}")
    except Exception:
        print(f"Unable to determine message count for Trash folder '{trash_folder}'.")

def move_unsubscribe_emails_to_trash(server, inbox_candidates, trash_folder, limit=10000):
  selected_folder = select_inbox_folder(server, inbox_candidates)
  print(f"Selected folder: {selected_folder}")
  print("Executing global index search for 'unsubscribe'...")

  messages = find_unsubscribe_message_ids(server, selected_folder)
  total_messages = len(messages)
  print(f"Total emails (aka 'pointers') found: {total_messages}")

  if total_messages == 0:
    print("No messages with unsubscribe found in Inbox")
    return

  if limit is not None and total_messages > limit:
    messages = messages[:limit]
    total_messages = len(messages)
    print(f"Limiting movement to the first {limit} messages.")

  for i in range(0, total_messages, CHUNK_SIZE):
    chunk = messages[i:i + CHUNK_SIZE]
    server.move(chunk, trash_folder)
    print(f"Moved batch {i} of {CHUNK_SIZE} emails to {trash_folder}")
    time.sleep(PAUSE_BETWEEN_CHUNKS)

  print(
    f"\nEmails with 'unsubscribe' moved to {trash_folder}. "
    "You can now review them there. You can move any you do not want to delete into another folder. "
    "Then you can empty the Trash folder and lower your email storage!"
  )

  # show current trash count after moving so user can decide about emptying
  trash_count = get_folder_status(server, trash_folder)
  if trash_count is None:
    print(f"Unable to determine message count for Trash folder '{trash_folder}'.")
  else:
    print(f"Current messages in {trash_folder}: {trash_count}")


def preview_unsubscribe_emails(server, inbox_candidates):
  selected_folder = select_inbox_folder(server, inbox_candidates)
  print(f"Selected folder: {selected_folder}")
  print("Executing preview search for 'unsubscribe'...")

  messages = find_unsubscribe_message_ids(server, selected_folder)
  total_messages = len(messages)
  print(f"Total matching messages in Inbox: {total_messages}")

  if total_messages == 0:
    print("No messages with unsubscribe found in Inbox.")


def _status_value(status, key):
  return int(status.get(key) or status.get(key.encode()) or 0)


def get_folder_uidnext(server, folder_name):
  status = server.folder_status(folder_name, ["UIDNEXT"])
  return _status_value(status, "UIDNEXT")


def find_unsubscribe_message_ids(server, folder_name=None):
  return search_in_uid_ranges(server, ["TEXT", "unsubscribe"], chunk_size=CHUNK_SIZE, folder_name=folder_name)


def search_in_uid_ranges(server, search_criteria, chunk_size=CHUNK_SIZE, folder_name=None):
  messages = []
  start = 1
  uidnext = None

  if folder_name is not None:
    server.select_folder(folder_name, readonly=True)
    try:
      uidnext = get_folder_uidnext(server, folder_name)
    except Exception:
      uidnext = None

  while True:
    end = start + chunk_size - 1
    if uidnext is not None and end >= uidnext:
      end = uidnext - 1

    query = ["UID", f"{start}:{end}"] + search_criteria
    try:
      chunk = server.search(query)
    except Exception:
      break

    if chunk:
      messages.extend(chunk)

    if uidnext is not None and end >= uidnext - 1:
      break

    if uidnext is None:
      # Continue until we hit a safe upper bound if UIDNEXT is unavailable.
      if not chunk and start > 1000000:
        break
      if not chunk and end >= 1000000:
        break

    start = end + 1
 

  return sorted(set(messages))


def get_folder_status_using_uid_pagination(server, folder_name, chunk_size=10000):
  total = 0
  start = 1
  uidnext = None

  try:
    uidnext = get_folder_uidnext(server, folder_name)
  except Exception:
    uidnext = None

  while True:
    end = start + chunk_size - 1
    if uidnext is not None and end >= uidnext:
      end = uidnext - 1

    try:
      server.select_folder(folder_name, readonly=True)
      chunk = server.search(["UID", f"{start}:{end}"])
    except Exception:
      break

    if not chunk:
      break

    total += len(chunk)

    if uidnext is not None and end >= uidnext - 1:
      break
    if uidnext is None and end >= 1000000:
      break

    start = end + 1

  return total


def get_folder_statuss(server, folder_name):
  total = 0
  unread = 0

  if True: #folder_name.lower() == "inbox":
      try:

        print(f"Scanning folder {folder_name} for total and unread counts...", flush=True)
        # Use folder_status to fetch counts instantly (No regex needed!)
        # Pass the folder name and a list/tuple of status keys
        status = server.folder_status(folder_name, ["MESSAGES", "UNSEEN"]) #"INBOX"
        
        # IMAPClient automatically parses the response into a clean dictionary
        total = status[b"MESSAGES"]
        unread = status[b"UNSEEN"]
        
        print(f"Folder: {folder_name}, Total: {total:,}, Unread: {unread :,}")

        #select_info = server.select_folder(folder_name, readonly=True)
        #total = _status_value(select_info, "EXISTS")
      except Exception:
        total = -1
        unread = -1

      return total, unread
 
 
  try:
    status = server.folder_status(folder_name, ["MESSAGES"])
    total = _status_value(status, "MESSAGES")
  except Exception:
    total = -1

  if total == 10000:
    try:
      total = get_folder_status_using_uid_pagination(server, folder_name)
    except Exception:
      pass

  try:
    status = server.folder_status(folder_name, ["UNSEEN"])
    unread = _status_value(status, "UNSEEN")
  except Exception:
    unread = -1

  if unread == -1:
    try:
      server.select_folder(folder_name, readonly=True)
      unread = len(server.search(["UNSEEN"]))
    except Exception:
      unread = -1

  return total, unread


def preview_unsubscribe_emails_with_trash(server, inbox_candidates, trash_folder):
  # select inbox and show total inbox count
  selected_folder = select_inbox_folder(server, inbox_candidates)
  print(f"Selected folder: {selected_folder}")

  inbox_total = get_folder_status(server, selected_folder)
  if inbox_total is None:
    print(f"Unable to determine total messages in Inbox folder '{selected_folder}'.")
  else:
    print(f"Total messages in {selected_folder}: {inbox_total}")

  # show matching messages in Inbox using the broader unsubscribe search
  print("Executing preview search for 'unsubscribe'...")
  messages = find_unsubscribe_message_ids(server, selected_folder)
  match_total = len(messages)
  print(f"Total matching messages in Inbox: {match_total}")
  if match_total == 0:
    print("No messages with unsubscribe found in Inbox.")

  # show current Trash count (heads-up)
  trash_count = get_folder_status(server, trash_folder)
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
      total, unread = get_folder_statuss(server, folder_name)
      unsubscribe_count=-2
      #unsubscribe_count = len(search_in_uid_ranges(server, ["TEXT", "unsubscribe"], folder_name=folder_name))
      print(
        f"Folder: {folder_name} | Total: {total} | Unread: {unread} | Unsubscribe: {unsubscribe_count}"
      )
      # Critical safety pause to stay under Yahoo's command-frequency firewall thresholds
      time.sleep(PAUSE_BETWEEN_CHUNKS)
    except Exception as e:
      print(f"Skipping {folder_name}: {e}")



# Safety Settings for Deletion
DELETE_CHUNK_SIZE = 2000
PAUSE_BETWEEN_DELETES = 1.0

def  empty_trash_folder2(server, trash_folder):
    """
    Safely purges the Trash folder in manageable blocks to prevent 
    Yahoo connection bans while permanently wiping out storage space.
    """
    print("WARNING: Emptying Trash will permanently delete all messages in the Trash folder.")
    confirmation = input("Type 'Yes' to proceed: ")
    if confirmation != "Yes":
      print("Aborting empty action.")
      return

    try:
        
        # 1. Open the Trash folder in read/write mode
        server.select_folder(trash_folder, readonly=False)
        print(f"\nOpened {trash_folder} for permanent purging...")
        
        # 2. Get all message UIDs currently residing in the Trash
        # Using ['ALL'] here is safe because we aren't performing a heavy text search,
        # we are just grabbing the raw tracking pointers.
        trash_uids = server.search(['ALL'])
        total_in_trash = len(trash_uids)
        
        print(f"Found {total_in_trash:,} messages waiting in {trash_folder}.")
        
        if total_in_trash == 0:
            print("Trash folder is already empty.")
            return

        # 3. Loop through the Trash UIDs in safe chunks
        print(f"Beginning permanent purge (Chunk Size: {DELETE_CHUNK_SIZE})...")
        for i in range(0, total_in_trash, DELETE_CHUNK_SIZE):
            chunk = trash_uids[i:i + DELETE_CHUNK_SIZE]
            
            # Step A: Add the system deletion flag to this specific batch
            server.add_flags(chunk, [b'\\Deleted'])
            
            # Step B: Tell Yahoo to immediately erase anything marked with \\Deleted
            # This physically frees up the storage space on Yahoo's server
            server.expunge()
            
            print(f" -> Permanently deleted batch starting at index {i:,} ({len(chunk)} emails)")
            
            # Anti-ban rest window
            time.sleep(PAUSE_BETWEEN_DELETES)
            
        print(f"🏁 Done! The {trash_folder} folder has been permanently emptied.")
        
    except Exception as e:
        print(f"An error occurred while emptying trash: {e}")


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

  if action not in {"preview", "clean","trash", "empty", "summary"}:
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
    server =  login_to_imap(email, config["imap_server_read"], config["imap_port_read"])
    server_write = login_to_imap(email, config["imap_server_write"], config["imap_port_write"])
    if action == "preview":
      preview_unsubscribe_emails_with_trash(server, config["inbox"], config["trash"])
    elif action == "trash":
      while True:
        total_moved = move_all_folder_messages(server, "Pre Trash", config["trash"])
        if total_moved == 0:
          break
    elif action == "clean":
      move_unsubscribe_emails_to_trash2(
        server,
        config["inbox"],
        "Pre Trash",
        limit,
      )
    elif action == "summary":
      summarize_folders(server)
    else:
      empty_trash_folder2(server, config["trash"])
  except Exception as error:
    print(f"\nUhoh! An exception occurred: {error}")
    traceback.print_exc()
    sys.exit(1)


if __name__ == "__main__":
  main()
