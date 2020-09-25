# i3blocks-spotmenu

Discount <a href="https://github.com/kmikiy/SpotMenu">SpotMenu</a> for use with <a href="https://github.com/vivien/i3blocks">i3blocks</a>/<a href="https://github.com/i3/i3">i3wm</a>.

<img src="/example.gif">

## Installation 

**i3blocks.conf**
```
[spotify]
command=python3 $HOME/Documents/i3blocks-spotmenu/spotmenu.py
interval=persist
```

## Issues 
* Open at cursor
  - Currently the window opening is hard coded for my monitors, needs to be adjusted based on where user clicks
