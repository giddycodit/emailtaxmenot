# emailtaxmenot
this little python program cleans out all emails in your provided email address box with the word "unsubscribe" in it. This has the effect of deleting subscription junk email, which often comprises the vast majority of our email.  It will  fully purge these emails so that they are deleted forever., which  will immediately reduce your storage.

to use:

Open a codespace on this project.

at the terminal prompt run:
pip install imapclient

then run:
python emailtaxmenot.py <your emailaddress> 

you will be prompted for your email  box password.*

* note - if you use multifactor, you will want to g
for Yahoo - go into Manage App Passwords when you are logged into your email box. You will want to select Other App from the dropdown and then type in emailtaxmenot and click Generate. Copy the generated 16 character password and use that as the password for this app.

For Gmail - go to Google Account settings. Select Security on the left menu. Under How you sign in to Google , click on 2-step Verification. Scroll to the very bottom of the page and click App Passwords. Type emailmenot as the app name and click Create. Copy the generated 16 character password and use that for the app.
