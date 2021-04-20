function send_action(self){
    url='http://10.0.0.17:9001';
    body = {"eventType":"dashboard",
    "user":"richard",
    "newState":self.name};
    fetch(url, {
        method: 'POST',
        mode: 'no-cors',
        body:JSON.stringify(body),
        headers: {'Content-Type': 'application/json'}
    }).catch((error) => console.error(error));
};
