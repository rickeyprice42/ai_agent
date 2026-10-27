import tkinter as tk


window = tk.Tk()
window.title("Avelin Test GUI")
window.geometry("420x220+80+80")
window.configure(bg="#f3f0e8")

status = tk.Label(
    window,
    text="Ready for safe desktop test",
    font=("Segoe UI", 14),
    bg="#f3f0e8",
    fg="#191919",
)
status.pack(pady=(50, 24))


def mark_clicked() -> None:
    status.configure(text="Clicked safely")


button = tk.Button(window, text="Continue", command=mark_clicked, width=18)
button.pack()
window.mainloop()
