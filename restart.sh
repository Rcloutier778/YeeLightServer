pkill yeelight_autoset.py
echo "Vlads service is disabled, need to enable it once you get IP"
sudo systemctl restart yeelight_autoset.service 
sudo systemctl restart yeelight_autoset_vlad.service

sudo /etc/init.d/cron restart

