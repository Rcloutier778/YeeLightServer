pkill yeelightpython.py
pkill yeelightServer.py
echo "Vlads service is disabled, need to enable it once you get IP"
sudo systemctl restart yeelight_server.service 
sudo systemctl restart yeelight_phone_vlad.service
sudo systemctl restart yeelight_phone_richard.service
sudo /etc/init.d/cron restart

